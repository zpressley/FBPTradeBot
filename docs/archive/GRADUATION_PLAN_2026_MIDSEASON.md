# Mid-Season Prospect Graduation Plan — July 2026

**Status: PLAN ONLY — nothing in this doc has been executed.** This is a reference for how to run the graduation of the 37 players below, written after reviewing the constitution, the `combined_players.json`/`player_log.json` schema, and the existing graduation code paths.

---

## 1. Rule basis (from the constitution)

- **Article 3, Section 05:** a prospect must graduate once they cross **any** of: 350 PA, 100 IP (or 30 pitching appearances), or turning 26.
- **Article 3/5, Section 05.2:** graduation is forced at the **MLB All-Star Break**; teams must roster the graduate on their Yahoo 26-man by "the first Monday when moves reset" after that, or forfeit the player to free agency.
- **Article 2, Section 02.2.iii:** a graduating **Purchased Contract (PC)** prospect is assigned a **TC-R** keeper contract ($5).
- **Article 3, Section 04.5:** a graduating **Blue-Chip Contract (BC)** prospect instead gets **TC-BC-1** ($5, same as TC-R for year one, but advances to TC-BC-2 next year and then TC-1 — a two-year runway instead of TC-R's one).

So "PC" / "BC" in your list are the two prospect contract tiers, and they graduate to *different* keeper tiers — this isn't cosmetic, it changes future WizBucks cost.

## 2. Exact target field values (confirmed from live code, not guessed)

`kap/kap_processor.py` already defines the canonical mapping (and its dollar cost — this is the same table `js/kap.js` mirrors on the frontend, so both sides agree):

| Group | `years_simple` (before) | `years_simple` (after) | `status` (after) | `contract_type` (after) | Cost |
|---|---|---|---|---|---|
| PC graduate | `P` | `TC R` | `[6] TCR` | `Keeper Contract` | $5 |
| BC graduate | `P` | `TC BC-1` | `[6] TCBC1` | `Keeper Contract` | $5 (→ $5 again next year as TC BC-2, then $15 as TC 1) |

All 37 players also need `player_type: "Farm"` → `"MLB"`.

**Important gap found:** `POST /api/admin/bulk-graduate` (the endpoint built for exactly this — see §5) sets `player_type` and `years_simple` and `contract_type`, but it **never sets `status`**. Today, zero players in `combined_players.json` have `status` = `[6] TCBC1` — this would be the first time that value is ever used, and the endpoint as written won't produce it. This needs a small code fix before we run this (see §5.1), otherwise all 37 players will show the correct `years_simple` but a stale `status` badge (still `[7] P` or whatever they had before) — inconsistent and confusing on the site.

## 3. The 37 players — cross-checked against `combined_players.json`

All 37 names matched a record. **34 of 37 need no data resolution** — team and contract tier in your list match the live data exactly. **3 need your confirmation before I touch anything** (see §4).

| Team (yours) | Player | Tag | upid | Current `contract_type` | Current `FBP_Team` | Target tier |
|---|---|---|---|---|---|---|
| WAR | Colson Montgomery | PC | 3522 | Purchased Contract | WAR ✓ | TC R |
| TBB | Cole Young | PC | 3901 | Purchased Contract | TBB ✓ | TC R |
| WIZ | Jakob Marsee | PC | 6555 | Purchased Contract | WIZ ✓ | TC R |
| DRO | Jac Caglianone | BC | 7685 | Blue Chip Contract | DRO ✓ | TC BC-1 |
| JEP | Luke Keaschall | BC | 6720 | Blue Chip Contract | JEP ✓ | TC BC-1 |
| SAD | Sal Stewart | BC | 3951 | Blue Chip Contract | SAD ✓ | TC BC-1 |
| **HAM** | **Brady House** | PC | 3482 | Purchased Contract | **DRO ⚠️** | TC R |
| DRO | Roman Anthony | BC | 3852 | Blue Chip Contract | DRO ✓ | TC BC-1 |
| SAD | Samuel Basallo | BC | 4593 | Blue Chip Contract | SAD ✓ | TC BC-1 |
| CFL | Carter Jensen | BC | 2237 | Blue Chip Contract | CFL ✓ | TC BC-1 |
| WIZ | Kevin McGonigle | BC | 6591 | Blue Chip Contract | WIZ ✓ | TC BC-1 |
| DMN | JJ Wetherholt | BC | 7619 | Blue Chip Contract | DMN ✓ | TC BC-1 |
| RV | Carson Benge | PC | 7591 | Purchased Contract | RV ✓ | TC R |
| DRO | T.J. Rumfield | PC | 4634 | Purchased Contract | DRO ✓ | TC R |
| DRO | Marcelo Mayer | PC | 3443 | Purchased Contract | DRO ✓ | TC R |
| RV | Kyle Teel | PC | 6573 | Purchased Contract | RV ✓ | TC R |
| WIZ | Max Muncy | PC | 4001 | Purchased Contract | WIZ ✓ | TC R |
| RV | Cam Schlittler | BC | 7568 | Blue Chip Contract | RV ✓ | TC BC-1 |
| **LFB** | **Zebby Matthews** | PC | 6988 | Purchased Contract | **DMN ⚠️** | TC R |
| WIZ | Braxton Ashcraft | BC | 6263 | Blue Chip Contract | WIZ ✓ | TC BC-1 |
| JEP | Jacob Misiorowski | BC | 3906 | Blue Chip Contract | JEP ✓ | TC BC-1 |
| SAD | Nolan McLean | BC | 6911 | Blue Chip Contract | SAD ✓ | TC BC-1 |
| DMN | Parker Messick | PC | 3913 | Purchased Contract | DMN ✓ | TC R |
| RV | Chase Burns | BC | 7602 | Blue Chip Contract | RV ✓ | TC BC-1 |
| LFB | Chase Dollander | PC | 6572 | Purchased Contract | LFB ✓ | TC R |
| RV | Bubba Chandler | PC | 3779 | Purchased Contract | RV ✓ | TC R |
| DMN | Cade Horton | BC | 3894 | Blue Chip Contract | DMN ✓ | TC BC-1 |
| JEP | Roki Sasaki | BC | 7540 | Blue Chip Contract | JEP ✓ | TC BC-1 |
| TBB | Connelly Early | BC | 6853 | Blue Chip Contract | TBB ✓ | TC BC-1 |
| DRO | Brandon Sproat | PC | 3930 | Purchased Contract | DRO ✓ | TC R |
| TBB | Rhett Lowder | PC | 6577 | Purchased Contract | TBB ✓ | TC R |
| DRO | Payton Tolle | PC | 7658 | Purchased Contract | DRO ✓ | TC R |
| SAD | Grant Taylor | BC | 6752 | Blue Chip Contract | SAD ✓ | TC BC-1 |
| LFB | Edgardo Henriquez | PC | 6075 | Purchased Contract | LFB ✓ | TC R |
| DRO | Bradgley Rodriguez | PC | 4611 | Purchased Contract | DRO ✓ | TC R |
| **HAM** | **Didier Fuentes** | **BC** | 7304 | **Purchased Contract ⚠️** | HAM ✓ | *TC R or TC BC-1?* |
| DRO | Anthony Nunez | PC | 7924 | Purchased Contract | DRO ✓ | TC R |

Also note: 5 of these 37 (Kevin McGonigle, JJ Wetherholt, Carson Benge, T.J. Rumfield, Anthony Nunez) still have `debuted: false` in the data — stale, since your PA/IP numbers prove they've clearly played in the majors. Worth setting `debuted: true` on these 5 as part of cleanup (not required for the graduation itself, but keeps other automation — `add_mlb_rookie_flag.py`, eligibility reports — accurate going forward).

## 4. Three flagged discrepancies — RESOLVED (2026-07-13, confirmed by Zach)

1. **Brady House** — confirmed **DRO is correct**. My list had a typo; no ownership change needed, no data fix needed.
2. **Zebby Matthews** — confirmed **DMN is correct**. Same — typo on my end, no data fix needed.
3. **Didier Fuentes** — confirmed he **should be Blue Chip**, i.e. the data's `contract_type: "Purchased Contract"` is itself wrong and should be `"Blue Chip Contract"`. Two implications:
   - He graduates to **TC BC-1** (added to `upid_tier_map` below), not TC-R.
   - Separate from the graduation call: his `contract_type` has been mislabeled since before this update (bulk-graduate will overwrite it to `"Keeper Contract"` regardless, so this doesn't block graduation) — but it's worth checking whether he was originally charged the wrong WizBucks amount when his prospect contract was assigned, since PC and BC prospect contracts aren't priced the same. Flagging for your judgment on whether that's worth a retroactive correction; not something I'm assuming needs fixing.

All 3 are now resolved — the plan below is final and ready to run pending your go-ahead on §5.1.

## 5. Recommended execution path

There's already a purpose-built endpoint for this: **`POST /api/admin/bulk-graduate`** in `api_admin_bulk.py`, reachable through the Cloudflare Worker at `https://fbp-auth.zpressley.workers.dev/api/admin/bulk-graduate` (already whitelisted in the worker's route table, no client-side API key needed — the worker injects it). It already:
- Holds `DATA_LOCK` while writing (safe against concurrent bot activity),
- Writes `combined_players.json`,
- Appends a `Graduate` entry to `player_log.json` for every player,
- Queues a git commit + push (durability, same pattern as everything else in this repo),
- Sends a Discord notification to the admin log channel listing who graduated,
- Supports exactly the split we need: a `contract_tier` default plus a per-player `upid_tier_map` override — its own code comment literally says `# Per-player overrides (BC → TC BC-1)`, i.e. it was built with this exact PC/BC distinction in mind.

This is a much better path than hand-editing `combined_players.json` directly: editing the git file in this working copy wouldn't be "live" until Railway redeploys, whereas hitting the running bot's endpoint updates production immediately and handles the commit/log/notification bookkeeping consistently with every other admin action in this system.

### 5.1 Prerequisite code fix (recommend doing this first, separately)

`bulk_graduate` never sets `p["status"]`, only `years_simple`/`player_type`/`contract_type`. Since this is the *first* time `TC BC-1` will ever be used, nothing currently maps it to `[6] TCBC1` at write time. I'd add ~4 lines to `api_admin_bulk.py` right where it sets `years_simple`, using the same `_KEY_TO_FIELDS`-style table `kap_processor.py` already has, so `status` and `years_simple` never drift apart again (this fixes it for *every* future graduation, not just this batch). I did not make this change — flagging it as a small, low-risk fix to approve separately, since it touches a live production file.

### 5.2 The actual call — FINAL, all §4 resolutions applied

```
POST https://fbp-auth.zpressley.workers.dev/api/admin/bulk-graduate
Content-Type: application/json

{
  "admin": "Zach",
  "contract_tier": "TC R",
  "upids": ["3522","3901","6555","7685","6720","3951","3482","3852","4593","2237",
            "6591","7619","7591","4634","3443","6573","4001","7568","6988","6263",
            "3906","6911","3913","7602","6572","3779","3894","7540","6853","3930",
            "6577","7658","6752","6075","4611","7304","7924"],
  "upid_tier_map": {
    "7685": "TC BC-1", "6720": "TC BC-1", "3951": "TC BC-1", "3852": "TC BC-1",
    "4593": "TC BC-1", "2237": "TC BC-1", "6591": "TC BC-1", "7619": "TC BC-1",
    "7568": "TC BC-1", "6263": "TC BC-1", "3906": "TC BC-1", "6911": "TC BC-1",
    "7602": "TC BC-1", "3894": "TC BC-1", "7540": "TC BC-1", "6853": "TC BC-1",
    "6752": "TC BC-1", "7304": "TC BC-1"
  }
}
```

18 players in `upid_tier_map` (17 originally-BC + Didier Fuentes, upid 7304, confirmed BC per §4.3). Everyone else (19 players) falls through to the `contract_tier` default (`TC R`), which is correct for all remaining PC players. 37 total.

**Separately** (not part of this call, not blocking it): consider whether Didier Fuentes' original prospect-contract WizBucks charge needs a retroactive correction, since the data has had him mislabeled as PC instead of BC. Your call.

### 5.3 Follow-up cleanup (separate, smaller calls/edits)

- Set `debuted: true` for the 5 stale records noted in §3 (small direct edit or a tiny script — not covered by bulk-graduate).
- 13 of these 37 are still listed in `data/top100_prospects.json` (e.g. Kevin McGonigle #2, JJ Wetherholt #5, Nolan McLean #6). Worth checking whether that file auto-regenerates from an external ranking feed (in which case it'll drop them on its own next refresh) or needs a manual prune — didn't trace that far, flagging so it doesn't get missed.
- `data/graduation_eligible.json` is a standing "needs review" report keyed by player name; these 37 should be removed from it or it'll keep flagging already-graduated players.
- One of the 37 (Payton Tolle) has a completed pick in `data/draft_order_2026.json` — that's just historical draft-pick record-keeping, nothing to change there.

## 6. Verification checklist after running

1. Spot-check a few upids in `combined_players.json`: `player_type=="MLB"`, `years_simple` and `status` both updated, `contract_type=="Keeper Contract"`.
2. Confirm 37 new `Graduate` entries landed in `player_log.json` (search `"update_type": "Graduate"` with today's timestamp).
3. Confirm the Discord admin-log channel got the "🎓 Bulk Graduate" notification.
4. Check whether `WEBSITE_REPO` / `WEBSITE_REPO_TOKEN` are set on Railway — if not, `sync_to_website()` silently no-ops and `fbp-hub`'s copy of `combined_players.json` will only catch up on the next daily sync (up to ~24h), not immediately. Worth confirming either way so there's no surprise lag on the live site.
5. Send managers a heads-up (constitution requires them to roster these 37 on their Yahoo 26-man by the first Monday moves reset after the All-Star break, or lose them to free agency) — the bulk-graduate Discord notification covers the "what happened," but the roster-compliance deadline itself is a separate manager-facing reminder worth sending.
