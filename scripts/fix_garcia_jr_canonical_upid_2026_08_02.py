#!/usr/bin/env python3
"""Correct the Luis Garcia Jr. WAR->WIZ trade to target UPID 8697, not 8696.

Background
----------
Two independent investigations this session both touched Luis Garcia Jr.
(Nationals 1B) without seeing the full picture, because each was working
from a different, incomplete snapshot of a fast-moving repo:

1. UPID 8696 was created by scripts/recover_lost_add_player_20260730.py,
   recovering Zach's original 7/30 3:59pm ET admin-portal add that got lost
   to the (now-fixed) fire-and-forget commit bug. That recovery deliberately
   left bio fields blank and -- this is the bug -- never gave the record an
   "FBP_Team" key at all (only "manager"), so it has read as unowned ever
   since, even after later enrichment filled in mlb_id/birth_date/etc.

2. Not realizing 8696 already existed (it looked broken/unowned in the UI),
   Zach re-added the same real player via the live admin portal at 7:55pm ET
   that same evening, creating UPID 8697 -- this time with FBP_Team/manager
   set correctly (WAR) and a real, distinct yahoo_id (10964). One minute
   later Zach used a UPID-merge tool to fold "8697" into "2899" (an unrelated
   real person, an Angels/Mets pitcher) -- but that merge only updated
   upid_database.json's identity/name index, not the actual roster row in
   combined_players.json, so 8697 still exists as its own live player record.
   The very next morning's Yahoo roster sync independently confirmed 8697 as
   WAR's real roster spot for this player ("In Season Add") and dropped the
   stale attribution on 2899 ("In Season Drop") -- origin/main's own data
   corroborates 8697, not 8696, as the live, correct record.

3. Warp's separate 7/31 investigation (TRADE_DATA_ISSUES_2026_07_31.md /
   scripts/apply_missing_trades_and_garcia_fix_2026_07_31.py) correctly found
   and fixed the *2899* contamination (wrong mlb_id/birth_date/team bleeding
   over from the real Nationals infielder) and correctly applied the
   WAR<->WIZ trade -- but it fetched before 8697 existed in its own working
   view and had no way to know about it, so it applied the trade to UPID 8696
   (the only Garcia record it knew about).

Net effect before this script: the WAR->WIZ trade landed on the orphaned,
never-synced 8696 instead of the live 8697 -- so on the actual site, Luis
Garcia Jr. would still show up owned by WAR, unchanged, while a duplicate,
invisible-looking record silently flipped to WIZ.

This script:
  - Retires UPID 8696 (clears FBP_Team/manager/status, so it reads as
    unowned/inert instead of a confusing half-populated ghost) and flags it
    `approved_dupes: "TRUE"` in upid_database.json, matching the existing
    convention already used for the other Luis Garcia duplicates (2898/3767).
  - Applies the real WAR->WIZ move to UPID 8697, and adds its missing
    `status` key ("[5] TC1"), matching every other Keeper Contract/TC1 player.
  - Repoints trades.json's MANUAL-20260731-003 transfer from upid 8696 to
    upid 8697, so trade history matches the actual roster data.
  - Leaves the earlier (mistaken) player_log Trade entry crediting 8696
    in place -- per this session's established convention of not rewriting
    history -- and appends correction entries explaining what happened,
    plus the real Trade entry for 8697.

Every write is guarded by an exact-match check against the expected
pre-fix value; already-correct values are skipped, not overwritten.

Run:
    python3 fix_garcia_jr_canonical_upid_2026_08_02.py --dry-run
    python3 fix_garcia_jr_canonical_upid_2026_08_02.py
"""

import json
import sys
from datetime import datetime, timezone

COMBINED_FILE = "data/combined_players.json"
PLAYER_LOG_FILE = "data/player_log.json"
TRADES_FILE = "data/trades.json"
UPID_DB_FILE = "data/upid_database.json"

NOW = datetime.now(timezone.utc)
NOW_ISO = NOW.isoformat().replace("+00:00", "Z")
SEASON = 2026
ADMIN = "zpressley"
TRADE_ID = "MANUAL-20260731-003"

# (upid, field, expected_before, new_value)
FIELD_FIXES = [
    # Retire 8696: clear ownership so it reads as unowned/inert, not a
    # confusing half-populated ghost that silently disagrees with 8697.
    ("8696", "FBP_Team", "WIZ", ""),
    ("8696", "manager", "Whiz Kids", ""),
    ("8696", "status", "[5] TC1", ""),
    # Apply the real trade to 8697, the live/synced record.
    ("8697", "FBP_Team", "WAR", "WIZ"),
    ("8697", "manager", "Weekend Warriors", "Whiz Kids"),
]

ADD_MISSING_KEY = {
    "8697": {"status": "[5] TC1"},
}


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path, data, ensure_ascii=False):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=ensure_ascii)


def make_log_entry(player, upid, event, update_type):
    return {
        "id": f"{SEASON}-{NOW_ISO}-UPID_{upid}-{update_type}-GarciaCorrection",
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
        "update_type": update_type,
        "event": event,
    }


def main():
    dry_run = "--dry-run" in sys.argv
    print(f"{'DRY RUN — ' if dry_run else ''}Correct Garcia Jr. canonical UPID (8696 -> 8697)\n" + "=" * 76)

    players = _load(COMBINED_FILE)
    player_log = _load(PLAYER_LOG_FILE)
    trades = _load(TRADES_FILE)
    upid_db = _load(UPID_DB_FILE)
    by_upid = {str(p.get("upid")): p for p in players}

    applied, skipped = 0, 0
    changed = set()

    print("\n-- Field fixes --")
    for upid, field, expected_before, new_value in FIELD_FIXES:
        player = by_upid.get(upid)
        if not player:
            print(f"upid {upid}: NOT FOUND — skipping")
            continue
        name = player.get("name", "?")
        actual = player.get(field)
        if actual == expected_before:
            print(f"  {name:20} {field:12} {actual!r} -> {new_value!r}  [APPLY]")
            if not dry_run:
                player[field] = new_value
            applied += 1
            changed.add(upid)
        elif actual == new_value:
            print(f"  {name:20} {field:12} already correct ({actual!r})  [SKIP]")
            skipped += 1
        else:
            print(f"  {name:20} {field:12} unexpected value {actual!r} (expected {expected_before!r}) — NOT touching  [SKIP]")
            skipped += 1

    print("\n-- Adding missing keys --")
    for upid, new_keys in ADD_MISSING_KEY.items():
        player = by_upid.get(upid)
        if not player:
            continue
        name = player.get("name", "?")
        for field, value in new_keys.items():
            if field in player:
                print(f"  {name:20} {field:12} key already exists ({player[field]!r})  [SKIP]")
                skipped += 1
                continue
            print(f"  {name:20} {field:12} <missing> -> {value!r}  [APPLY]")
            if not dry_run:
                player[field] = value
            applied += 1
            changed.add(upid)

    print("\n-- upid_database.json: flag 8696 as an approved duplicate --")
    entry = upid_db.get("by_upid", {}).get("8696")
    if entry is None:
        print("  upid 8696 not found in upid_database.json — skipping")
    elif entry.get("approved_dupes") == "FALSE":
        print(f"  8696 approved_dupes: 'FALSE' -> 'TRUE'  [APPLY]")
        if not dry_run:
            entry["approved_dupes"] = "TRUE"
        applied += 1
    elif entry.get("approved_dupes") == "TRUE":
        print(f"  8696 approved_dupes already 'TRUE'  [SKIP]")
        skipped += 1
    else:
        print(f"  8696 approved_dupes unexpected value {entry.get('approved_dupes')!r} — NOT touching  [SKIP]")
        skipped += 1

    print("\n-- trades.json: repoint MANUAL-20260731-003's Garcia leg to 8697 --")
    rec = trades.get(TRADE_ID)
    repointed = False
    if rec is None:
        print(f"  {TRADE_ID} not found — skipping")
    else:
        for transfer in rec.get("transfers", []):
            if transfer.get("type") == "player" and str(transfer.get("upid")) == "8696":
                print(f"  {TRADE_ID}: transfer upid 8696 -> 8697  [APPLY]")
                if not dry_run:
                    transfer["upid"] = "8697"
                repointed = True
                applied += 1
        if not repointed:
            existing_upids = [t.get("upid") for t in rec.get("transfers", []) if t.get("type") == "player"]
            if "8697" in existing_upids:
                print(f"  {TRADE_ID} already references 8697  [SKIP]")
                skipped += 1
            else:
                print(f"  {TRADE_ID}: no 8696 transfer found to repoint (upids present: {existing_upids}) — NOT touching  [SKIP]")
                skipped += 1

    print("\n-- Player log entries --")
    existing_log_ids = {e.get("id") for e in player_log}
    log_added = 0

    p8696 = by_upid.get("8696")
    if p8696 is not None and "8696" in changed:
        entry = make_log_entry(p8696, "8696", "", "Admin")
        entry["event"] = (
            "Data hygiene: UPID 8696 retired as a duplicate of UPID 8697. Both "
            "represent Luis Garcia Jr. (WSH 1B) -- 8696 recovered a lost "
            "admin-portal add (see recover_lost_add_player_20260730.py), while "
            "8697 was added independently later the same evening (2026-07-30) "
            "and is the properly-owned, Yahoo-sync-confirmed live record. The "
            "WAR->WIZ trade (MANUAL-20260731-003), earlier misapplied to 8696 "
            "by an investigation that didn't yet know 8697 existed, has been "
            "repointed to 8697. 8696's FBP_Team/manager/status cleared so it "
            "no longer shows as a confusing half-owned duplicate."
        )
        if entry["id"] not in existing_log_ids:
            print(f"  8696 hygiene/correction note  [APPLY]")
            if not dry_run:
                player_log.append(entry)
            log_added += 1
        else:
            print(f"  8696 hygiene entry already present  [SKIP]")

    p8697 = by_upid.get("8697")
    if p8697 is not None and "8697" in changed:
        entry = make_log_entry(p8697, "8697", "", "Trade")
        entry["event"] = f"{TRADE_ID}: WAR->WIZ (corrected to the live record, 2026-08-02)"
        if entry["id"] not in existing_log_ids:
            print(f"  8697 Trade entry: WAR->WIZ  [APPLY]")
            if not dry_run:
                player_log.append(entry)
            log_added += 1
        else:
            print(f"  8697 trade entry already present  [SKIP]")

    print("\n" + "=" * 76)
    print(f"Field writes applied: {applied}  |  skipped: {skipped}")
    print(f"Player log entries added: {log_added}")

    if dry_run:
        print("\nDry run — no files written. Re-run without --dry-run to apply.")
        return

    _save(COMBINED_FILE, players, ensure_ascii=True)
    print(f"\nWrote {COMBINED_FILE}")
    _save(PLAYER_LOG_FILE, player_log, ensure_ascii=False)
    print(f"Wrote {PLAYER_LOG_FILE}")
    _save(TRADES_FILE, trades, ensure_ascii=False)
    print(f"Wrote {TRADES_FILE}")
    _save(UPID_DB_FILE, upid_db, ensure_ascii=False)
    print(f"Wrote {UPID_DB_FILE}")


if __name__ == "__main__":
    main()
