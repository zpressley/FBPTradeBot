#!/usr/bin/env python3
"""Apply 4 trades that never entered the portal, plus the Luis Garcia yahoo_id/bio
dedup, per Zach's 2026-07-31 review.

Background
----------
Investigation (see TRADE_DATA_ISSUES_2026_07_31.md) found:

1. Three trades from 7/29-7/30 (McGreevy CFL->B2J; Luis Garcia Jr./Hall/Cunningham
   WAR<->WIZ; Joe Ryan/Pratt/Roupp B2J<->SAD) never created a record in
   data/trades.json or data/pending_trades.json at all -- they were never
   submitted through the trade portal (config/season_dates.json's
   trade_deadline was wrong, and even correct wouldn't support the "noon
   Eastern" cutoff Zach actually wants -- see companion report on the
   underlying fix). Zach confirmed these are real, legitimate trades that
   should be applied.

2. Cam Caminiti's trade (TRADE-250426_2025-024, LFB->HAM for Bryce Miller)
   correctly expired in-app because HAM never clicked accept -- but Zach has
   now confirmed this trade is real and should be completed. The original
   trade record is left untouched (accurate history of what happened in the
   portal); this script applies the trade directly, same as the other three.

3. yahoo_id "9455" is shared by 3 different real players (upid 2898, 2899,
   3767) in combined_players.json, which is why the daily Yahoo roster sync
   (data_pipeline/roster_sync.py, _build_combined_indexes) keeps resolving to
   the wrong one. Cross-checking data/yahoo_player_index.json (Yahoo's own
   data) shows yahoo_id 9455 is actually "Luis Garcia", team NYM (Mets), RP --
   not LAA as combined_players.json says. Web search + the birth_date already
   on upid 2899 (2000-05-16) confirm mlb_id 671277 belongs to the real
   Nationals infielder Luis Garcia Jr. (born 2000-05-16), NOT to the Mets
   reliever who actually owns yahoo_id 9455 -- that mlb_id/birth_date/
   mlb_primary_position was mismatched onto upid 2899 by a name-only bio
   enrichment pass. This script:
     - Moves mlb_id 671277 + birth_date 2000-05-16 onto UPID 8696 (the new,
       correctly-created Luis Garcia Jr. record), where they actually belong.
     - Clears those same contaminated fields off upid 2899 (left blank for a
       proper team-aware bio re-enrichment later -- not guessing a
       replacement value).
     - Corrects team LAA -> NYM on 2898/2899 (per Yahoo's own live data).
     - Clears yahoo_id 9455 off 2898 and 3767 so only 2899 -- the one Zach
       actually owns/rosters -- keeps it. This is the fix for "adds the wrong
       Luis Garcia from LAA."
   yahoo_id is deliberately NOT guessed for UPID 8696 -- Yahoo's internal
   fantasy player_id isn't safely derivable from public sources, and guessing
   IDs into this file is exactly the mistake that caused today's mess.

Every field write is guarded by an exact-match check against the expected
pre-fix value (same pattern as scripts/restore_pc_bc_corruption_2026_07.py).
If a field has changed since diagnosis, it's left alone and reported as
SKIPPED rather than blindly overwritten.

Run:
    python3 scripts/apply_missing_trades_and_garcia_fix_2026_07_31.py --dry-run
    python3 scripts/apply_missing_trades_and_garcia_fix_2026_07_31.py
"""

import json
import sys
from datetime import datetime, timezone

COMBINED_FILE = "data/combined_players.json"
PLAYER_LOG_FILE = "data/player_log.json"
TRADES_FILE = "data/trades.json"

NOW = datetime.now(timezone.utc)
NOW_ISO = NOW.isoformat().replace("+00:00", "Z")
SEASON = 2026
ADMIN = "zpressley"

MANAGER_NAME = {
    "WAR": "Weekend Warriors",
    "WIZ": "Whiz Kids",
    "B2J": "Btwn2Jackies",
    "SAD": "not much of a donkey",
    "CFL": "Country Fried Lamb",
    "HAM": "Hammers",
    "LFB": "La Flama Blanca",
}

# ---------------------------------------------------------------------------
# 1. Player ownership/contract field fixes (trades + McGreevy backfill)
# Each entry: upid -> list of (field, expected_before, new_value) guarded writes
# ---------------------------------------------------------------------------
FIELD_FIXES = {
    # --- Cam Caminiti <-> Bryce Miller (LFB <-> HAM) ---
    "7347": [("FBP_Team", "LFB", "HAM"), ("manager", "La Flama Blanca", "Hammers")],
    "2211": [("FBP_Team", "HAM", "LFB"), ("manager", "Hammers", "La Flama Blanca")],

    # --- McGreevy: unowned -> B2J, plus contract backfill ---
    "2979": [
        ("FBP_Team", "", "B2J"),
        ("manager", "", "Btwn2Jackies"),
        ("contract_type", "", "Keeper Contract"),
        ("years_simple", "P", "TC 1"),
        ("status", "[9] P", "[5] TC1"),
        ("NRI", "CFL", ""),
    ],

    # --- Luis Garcia Jr./Hall/Cunningham (WAR <-> WIZ) ---
    # NOTE: as of this run, upid 8696 already has contract_type/years_simple/
    # mlb_id/birth_date/debut_date populated (someone re-ran the admin portal's
    # enrichment lookup between the diagnosis and this fix, per the original
    # recovery script's own suggestion) -- confirmed correct against the same
    # real-world data this investigation independently found. Only re-type the
    # mlb_id (currently a string, everywhere else in this file it's an int).
    "8696": [
        ("manager", "WAR", "Whiz Kids"),
        ("mlb_id", "671277", 671277),
    ],
    "7951": [("FBP_Team", "WIZ", "WAR"), ("manager", "Whiz Kids", "Weekend Warriors")],
    "7953": [("FBP_Team", "WIZ", "WAR"), ("manager", "Whiz Kids", "Weekend Warriors")],

    # --- Joe Ryan/Pratt/Roupp (B2J <-> SAD) ---
    "4016": [("FBP_Team", "B2J", "SAD"), ("manager", "Btwn2Jackies", "not much of a donkey")],
    "4567": [("FBP_Team", "SAD", "B2J"), ("manager", "not much of a donkey", "Btwn2Jackies")],
    "6583": [("FBP_Team", "SAD", "B2J"), ("manager", "not much of a donkey", "Btwn2Jackies")],

    # --- Luis Garcia yahoo_id/team/bio dedup (data hygiene, not a trade) ---
    "2898": [("yahoo_id", "9455", ""), ("team", "LAA", "NYM")],
    "2899": [
        ("team", "LAA", "NYM"),
        ("mlb_id", 671277, None),
        ("birth_date", "2000-05-16", None),
        ("mlb_primary_position", "2B", None),
    ],
    "3767": [("yahoo_id", "9455", "")],
}

# UPID 8696 is missing the FBP_Team and status keys entirely (not blank --
# absent). Handled separately since there's no "before" value to guard against.
ADD_MISSING_KEY = {
    "8696": {"FBP_Team": "WIZ", "status": "[5] TC1"},
}

# Player-log "Trade" entries: (upid, from_team, to_team, trade_label)
TRADE_LOG_ENTRIES = [
    ("7347", "LFB", "HAM", "MANUAL-20260731-001"),
    ("2211", "HAM", "LFB", "MANUAL-20260731-001"),
    ("2979", "CFL", "B2J", "MANUAL-20260731-002"),
    ("8696", "WAR", "WIZ", "MANUAL-20260731-003"),
    ("7951", "WIZ", "WAR", "MANUAL-20260731-003"),
    ("7953", "WIZ", "WAR", "MANUAL-20260731-003"),
    ("4016", "B2J", "SAD", "MANUAL-20260731-004"),
    ("4567", "SAD", "B2J", "MANUAL-20260731-004"),
    ("6583", "SAD", "B2J", "MANUAL-20260731-004"),
]

# Non-trade data-hygiene log entries: (upid, note)
HYGIENE_LOG_ENTRIES = [
    ("2898", "Data hygiene: cleared duplicate yahoo_id 9455 (belongs to upid 2899), corrected team LAA->NYM per Yahoo data"),
    ("2899", "Data hygiene: corrected team LAA->NYM per Yahoo data; cleared mlb_id/birth_date/mlb_primary_position mismatched from the Nationals Luis Garcia Jr. (moved to upid 8696)"),
    ("3767", "Data hygiene: cleared duplicate yahoo_id 9455 (belongs to upid 2899)"),
]

# New trades.json backfill records: trade_id -> record
TRADE_RECORDS = {
    "MANUAL-20260731-001": {
        "teams": ["LFB", "HAM"], "initiator_team": "LFB",
        "transfers": [
            {"type": "player", "upid": "7347", "from_team": "LFB", "to_team": "HAM"},
            {"type": "player", "upid": "2211", "from_team": "HAM", "to_team": "LFB"},
        ],
        "receives": {"LFB": ["SP Bryce Miller [SEA] [TC 1]"], "HAM": ["SP Cam Caminiti [ATL] [DC]"]},
        "note": "Manually applied 2026-07-31 by commissioner. Original portal submission "
                "TRADE-250426_2025-024 expired unaccepted by HAM, but Zach confirmed the "
                "deal is real and directed it be applied. Bryce Miller's leg (HAM->LFB) was "
                "found already satisfied at application time (he's on LFB via an apparently "
                "separate, unrelated transaction) -- no field write or player_log entry was "
                "made for him, only for Cam Caminiti.",
        "original_trade_id": "TRADE-250426_2025-024",
    },
    "MANUAL-20260731-002": {
        "teams": ["CFL", "B2J"], "initiator_team": "CFL",
        "transfers": [
            {"type": "player", "upid": "2979", "from_team": "CFL", "to_team": "B2J"},
        ],
        "receives": {"B2J": ["SP Michael McGreevy [STL] [TC 1]"], "CFL": []},
        "note": "Manually applied 2026-07-31. Never submitted through the trade portal "
                "(no record existed in trades.json/pending_trades.json). McGreevy also "
                "had his contract/status backfilled to TC1 in this same fix.",
    },
    "MANUAL-20260731-003": {
        "teams": ["WAR", "WIZ"], "initiator_team": "WAR",
        "transfers": [
            {"type": "player", "upid": "8696", "from_team": "WAR", "to_team": "WIZ"},
            {"type": "player", "upid": "7951", "from_team": "WIZ", "to_team": "WAR"},
            {"type": "player", "upid": "7953", "from_team": "WIZ", "to_team": "WAR"},
        ],
        "receives": {
            "WIZ": ["1B Luis García Jr. [WSH] [TC 1]"],
            "WAR": ["SS Steele Hall [CIN] [BC]", "SS Kayson Cunningham [AZ] [DC]"],
        },
        "note": "Manually applied 2026-07-31. Never submitted through the trade portal "
                "(no record existed in trades.json/pending_trades.json), likely because "
                "Luis Garcia Jr. did not yet exist as a valid, ownable player record at "
                "submission time.",
    },
    "MANUAL-20260731-004": {
        "teams": ["B2J", "SAD"], "initiator_team": "B2J",
        "transfers": [
            {"type": "player", "upid": "4016", "from_team": "B2J", "to_team": "SAD"},
            {"type": "player", "upid": "4567", "from_team": "SAD", "to_team": "B2J"},
            {"type": "player", "upid": "6583", "from_team": "SAD", "to_team": "B2J"},
        ],
        "receives": {
            "SAD": ["SP Joe Ryan [MIN] [VC 2]"],
            "B2J": ["SP Landen Roupp [SF] [TC 1]", "SS Cooper Pratt [MIL] [DC]"],
        },
        "note": "Manually applied 2026-07-31. Never submitted through the trade portal "
                "(no record existed in trades.json/pending_trades.json).",
    },
}


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path, data, ensure_ascii=False):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=ensure_ascii)


def make_trade_log_entry(player, upid, from_team, to_team, trade_id):
    return {
        "id": f"{SEASON}-{NOW_ISO}-UPID_{upid}-Trade-{trade_id}",
        "season": SEASON,
        "source": "manual_trade_application",
        "admin": ADMIN,
        "timestamp": NOW_ISO,
        "upid": upid,
        "player_name": player.get("name") or "",
        "team": player.get("team") or "",
        "pos": player.get("position") or "",
        "age": player.get("age"),
        "level": str(player.get("level") or ""),
        "team_rank": player.get("team_rank"),
        "rank": player.get("rank"),
        "eta": str(player.get("eta") or ""),
        "player_type": player.get("player_type") or "",
        "owner": player.get("manager") or "",
        "contract": player.get("contract_type") or "",
        "status": player.get("status") or "",
        "years": player.get("years_simple") or "",
        "update_type": "Trade",
        "event": f"{trade_id}: {from_team}->{to_team} (manual commissioner fix, 2026-07-31)",
    }


def make_hygiene_log_entry(player, upid, note):
    return {
        "id": f"{SEASON}-{NOW_ISO}-UPID_{upid}-Admin-DataHygiene",
        "season": SEASON,
        "source": "manual_data_fix",
        "admin": ADMIN,
        "timestamp": NOW_ISO,
        "upid": upid,
        "player_name": player.get("name") or "",
        "team": player.get("team") or "",
        "pos": player.get("position") or "",
        "age": player.get("age"),
        "level": str(player.get("level") or ""),
        "team_rank": player.get("team_rank"),
        "rank": player.get("rank"),
        "eta": str(player.get("eta") or ""),
        "player_type": player.get("player_type") or "",
        "owner": player.get("manager") or "",
        "contract": player.get("contract_type") or "",
        "status": player.get("status") or "",
        "years": player.get("years_simple") or "",
        "update_type": "Admin",
        "event": note,
    }


def main():
    dry_run = "--dry-run" in sys.argv
    print(f"{'DRY RUN — ' if dry_run else ''}Apply missing trades + Luis Garcia fix\n" + "=" * 76)

    players = _load(COMBINED_FILE)
    player_log = _load(PLAYER_LOG_FILE)
    trades = _load(TRADES_FILE)
    by_upid = {str(p.get("upid")): p for p in players}

    applied, skipped = 0, 0
    changed_upids = set()  # upids where at least one real field change was applied this run

    print("\n-- Field fixes --")
    for upid, fixes in FIELD_FIXES.items():
        player = by_upid.get(upid)
        if not player:
            print(f"upid {upid}: NOT FOUND — skipping entirely")
            continue
        name = player.get("name", "?")
        for field, expected_before, new_value in fixes:
            actual = player.get(field)
            if actual == expected_before:
                print(f"  {name:22} {field:20} {actual!r} -> {new_value!r}  [APPLY]")
                if not dry_run:
                    player[field] = new_value
                applied += 1
                changed_upids.add(upid)
            elif actual == new_value:
                print(f"  {name:22} {field:20} already correct ({actual!r})  [SKIP]")
                skipped += 1
            else:
                print(f"  {name:22} {field:20} unexpected current value {actual!r} "
                      f"(expected {expected_before!r}) — NOT touching  [SKIP]")
                skipped += 1

    print("\n-- Adding missing keys --")
    for upid, new_keys in ADD_MISSING_KEY.items():
        player = by_upid.get(upid)
        if not player:
            print(f"upid {upid}: NOT FOUND — skipping entirely")
            continue
        name = player.get("name", "?")
        for field, value in new_keys.items():
            if field in player:
                print(f"  {name:22} {field:20} key already exists ({player[field]!r}) — NOT touching  [SKIP]")
                skipped += 1
                continue
            print(f"  {name:22} {field:20} <missing> -> {value!r}  [APPLY]")
            if not dry_run:
                player[field] = value
            applied += 1
            changed_upids.add(upid)

    print("\n-- Player log: trade entries --")
    print("    (only logged for legs where a field actually changed this run --")
    print("     e.g. Bryce Miller is skipped below if he's already at his target")
    print("     team via an unrelated transaction, so we don't fabricate history)")
    log_added = 0
    existing_log_ids = {e.get("id") for e in player_log}
    for upid, from_team, to_team, trade_label in TRADE_LOG_ENTRIES:
        player = by_upid.get(upid)
        if not player:
            continue
        if upid not in changed_upids:
            print(f"  {player.get('name'):22} no field actually changed this run — not logging a trade entry  [SKIP]")
            continue
        entry = make_trade_log_entry(player, upid, from_team, to_team, trade_label)
        if entry["id"] in existing_log_ids:
            print(f"  {player.get('name'):22} log entry already present  [SKIP]")
            continue
        print(f"  {player.get('name'):22} {trade_label}: {from_team}->{to_team}  [APPLY]")
        if not dry_run:
            player_log.append(entry)
        log_added += 1

    print("\n-- Player log: data hygiene entries --")
    for upid, note in HYGIENE_LOG_ENTRIES:
        player = by_upid.get(upid)
        if not player:
            continue
        if upid not in changed_upids:
            print(f"  {player.get('name'):22} no field actually changed this run — not logging  [SKIP]")
            continue
        entry = make_hygiene_log_entry(player, upid, note)
        if entry["id"] in existing_log_ids:
            print(f"  {player.get('name'):22} hygiene log entry already present  [SKIP]")
            continue
        print(f"  {player.get('name'):22} {note[:60]}...  [APPLY]")
        if not dry_run:
            player_log.append(entry)
        log_added += 1

    print("\n-- trades.json backfill records --")
    trades_added = 0
    for trade_id, rec in TRADE_RECORDS.items():
        if trade_id in trades:
            print(f"  {trade_id} already present  [SKIP]")
            continue
        full_rec = {
            "trade_id": trade_id,
            "teams": rec["teams"],
            "initiator_team": rec["initiator_team"],
            "status": "approved",
            "created_at": NOW_ISO,
            "expires_at": NOW_ISO,
            "transfers": rec["transfers"],
            "acceptances": rec["teams"],
            "receives": rec["receives"],
            "discord": {"thread_id": None, "thread_url": None, "admin_review_message_id": None},
            "manager_approved_at": NOW_ISO,
            "admin_decision_by": ADMIN,
            "processed_at": NOW_ISO,
            "data_applied_at": NOW_ISO,
            "data_applied_by": ADMIN,
            "data_applied_summary": {
                "player_moves": len([t for t in rec["transfers"] if t["type"] == "player"]),
                "player_log_entries": len([t for t in rec["transfers"] if t["type"] == "player"]),
                "pick_moves": 0,
                "wb_transfers": 0,
                "buyins_purchased": 0,
                "warnings": [],
            },
            "manual_fix_note": rec["note"],
        }
        if "original_trade_id" in rec:
            full_rec["original_trade_id"] = rec["original_trade_id"]
        print(f"  {trade_id}: {rec['teams']}  [APPLY]")
        if not dry_run:
            trades[trade_id] = full_rec
        trades_added += 1

    print("\n" + "=" * 76)
    print(f"Field writes applied: {applied}  |  skipped: {skipped}")
    print(f"Player log entries added: {log_added}")
    print(f"Trade records added: {trades_added}")

    if dry_run:
        print("\nDry run — no files written. Re-run without --dry-run to apply.")
        return

    _save(COMBINED_FILE, players, ensure_ascii=False)
    print(f"\nWrote {COMBINED_FILE}")
    _save(PLAYER_LOG_FILE, player_log, ensure_ascii=False)
    print(f"Wrote {PLAYER_LOG_FILE}")
    _save(TRADES_FILE, trades, ensure_ascii=False)
    print(f"Wrote {TRADES_FILE}")

    print("\nNext steps (not run automatically):")
    print("  1. Review: git diff")
    print("  2. Commit: git add data/ && git commit -m 'Manual fix: 4 trades + Luis Garcia yahoo_id/bio dedup (2026-07-31)'")
    print("  3. Push: git push")


if __name__ == "__main__":
    main()
