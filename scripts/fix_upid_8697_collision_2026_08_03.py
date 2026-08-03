#!/usr/bin/env python3
"""Fix the UPID 8697 collision between Luis Garcia Jr. and Ramon Marquez
created on 2026-08-03.

Root cause (api_admin_bulk.py add_player(), and identically in
api_manager_players.py's _create_player_from_request_record() and
api_upid.py's create_upid_record() -- all three fixed separately in this
same batch): "next free UPID" was computed by scanning ONLY
upid_database.json's by_upid dict, never cross-referencing the UPIDs
actually present in data/combined_players.json. upid_database.json's
by_upid was missing an "8697" key even though Luis Garcia Jr. already
held UPID 8697 in combined_players.json, so when Ramon Marquez was added
via the admin tool, the generator saw no collision and handed him the
same UPID Luis Garcia Jr. already had. Any {upid: player} dict built from
combined_players.json's array silently keeps whichever of the two
same-UPID rows comes later and drops the other -- this is almost
certainly why the live site announced an auction bid as being on "Luis
Garcia Jr." when it was actually meant to be on Ramon Marquez.

This script:
  1. data/combined_players.json -- reassigns Ramon Marquez's row from
     upid 8697 to upid 8698 (confirmed genuinely free in
     upid_database.json). Luis Garcia Jr.'s row is left untouched.
  2. data/upid_database.json -- restores by_upid["8697"] to describe
     Luis Garcia Jr. (it had been overwritten to describe Ramon Marquez
     when his record was created), adds by_upid["8698"] for Ramon
     Marquez, and repoints name_index["ramon marquez"] from 8697 to 8698.
  3. data/auction_current.json -- the one live, unresolved bid
     (bid-7f3008e91865, $10 OB by WAR) had prospect_id "8697"; repointed
     to "8698" so it now correctly targets Ramon Marquez. The auction is
     still in an open "ob_window" phase -- nothing has resolved/changed
     ownership yet, so this is a clean fix with no downstream cleanup
     needed.
  4. data/player_log.json -- appends one new DataFix entry documenting
     the reassignment (does not edit the original, now-understood
     UPID_8697/Ramon Marquez creation entry -- that entry is left as an
     accurate historical record of what happened at the time).

NOT touched by this script (pre-existing, unrelated to today's incident):
  - upid_database.json's by_upid["8696"] (a separate, already-known
    "Luis García Jr." legacy/approved-dupe stub from an earlier
    engagement this season) and name_index["luis garcia jr."] (currently
    -> ["2897", "2899"], i.e. it already didn't list 8696 or 8697 even
    before today -- a pre-existing name_index staleness gap, not
    something this incident caused or that this script attempts to fix).
  - data/trades.json's existing "8697" reference (Luis Garcia Jr.'s
    legitimate WAR->WIZ trade from earlier this engagement) -- confirmed
    unaffected by this collision.

Guarded/idempotent: every write checks the current state matches what's
expected before mutating, and re-running after a successful apply is a
safe no-op (reports "already applied", changes nothing).

Run:
    python3 scripts/fix_upid_8697_collision_2026_08_03.py --dry-run
    python3 scripts/fix_upid_8697_collision_2026_08_03.py
"""

import json
import sys
from datetime import datetime, timezone

COMBINED_PATH = "data/combined_players.json"
UPID_DB_PATH = "data/upid_database.json"
AUCTION_PATH = "data/auction_current.json"
PLAYER_LOG_PATH = "data/player_log.json"

OLD_UPID = "8697"
NEW_UPID = "8698"
BID_ID = "bid-7f3008e91865"

GARCIA_MLB_ID = "671277"
MARQUEZ_MLB_ID = "827408"

GARCIA_IDENTITY = {
    "upid": OLD_UPID,
    "name": "Luis Garcia Jr.",
    "team": "WSH",
    "pos": "1B",
    "alt_names": [],
    "approved_dupes": "FALSE",
}


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path, data):
    # combined_players.json / upid_database.json / player_log.json are all
    # confirmed ensure_ascii=True on disk (escaped unicode); matching that
    # for auction_current.json too even though it has no unicode content.
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=True)


def main():
    dry_run = "--dry-run" in sys.argv
    label = "DRY RUN — " if dry_run else ""
    print(f"{label}Fix UPID {OLD_UPID} collision (Luis Garcia Jr. / Ramon Marquez)\n" + "=" * 78)

    changes = []  # (path, data) pairs to save at the end, only if not dry_run

    # -------------------------------------------------------------------
    # 1. combined_players.json
    # -------------------------------------------------------------------
    players = _load(COMBINED_PATH)
    rows_8697 = [p for p in players if str(p.get("upid")) == OLD_UPID]
    rows_8698 = [p for p in players if str(p.get("upid")) == NEW_UPID]

    marquez_row = next((p for p in rows_8697 if str(p.get("mlb_id")) == MARQUEZ_MLB_ID), None)
    garcia_row = next((p for p in rows_8697 if str(p.get("mlb_id")) == GARCIA_MLB_ID), None)
    already_fixed_combined = (
        marquez_row is None
        and garcia_row is not None
        and len(rows_8697) == 1
        and any(str(p.get("mlb_id")) == MARQUEZ_MLB_ID for p in rows_8698)
    )

    if already_fixed_combined:
        print(f"  combined_players.json: already fixed (Marquez at {NEW_UPID}, Garcia at {OLD_UPID})  [SKIP]")
    elif marquez_row is not None and garcia_row is not None and len(rows_8697) == 2:
        print(f"  combined_players.json: {len(rows_8697)} rows at upid {OLD_UPID} "
              f"(Garcia mlb_id={GARCIA_MLB_ID}, Marquez mlb_id={MARQUEZ_MLB_ID}) "
              f"-- reassigning Marquez's row to upid {NEW_UPID}  [APPLY]")
        if not dry_run:
            marquez_row["upid"] = NEW_UPID
            changes.append((COMBINED_PATH, players))
    else:
        print(f"  combined_players.json: unexpected state ({len(rows_8697)} rows at {OLD_UPID}, "
              f"garcia_row={'found' if garcia_row else 'MISSING'}, "
              f"marquez_row={'found' if marquez_row else 'MISSING'}) -- ABORTING, no files touched.")
        sys.exit(1)

    # -------------------------------------------------------------------
    # 2. upid_database.json
    # -------------------------------------------------------------------
    upid_db = _load(UPID_DB_PATH)
    by_upid = upid_db.get("by_upid", {})
    name_index = upid_db.get("name_index", {})

    current_8697 = by_upid.get(OLD_UPID)
    current_8698 = by_upid.get(NEW_UPID)

    already_fixed_db = (
        current_8697 is not None and current_8697.get("name") == "Luis Garcia Jr."
        and current_8698 is not None and current_8698.get("name") == "Ramon Marquez"
    )

    if already_fixed_db:
        print(f"  upid_database.json: already fixed (by_upid[{OLD_UPID}]=Garcia, by_upid[{NEW_UPID}]=Marquez)  [SKIP]")
    elif current_8697 is not None and current_8697.get("name") == "Ramon Marquez" and current_8698 is None:
        marquez_identity = dict(current_8697)
        marquez_identity["upid"] = NEW_UPID
        print(f"  upid_database.json: by_upid[{OLD_UPID}] wrongly describes Ramon Marquez -- "
              f"restoring to Luis Garcia Jr., moving Marquez's identity to by_upid[{NEW_UPID}]  [APPLY]")
        if not dry_run:
            by_upid[OLD_UPID] = dict(GARCIA_IDENTITY)
            by_upid[NEW_UPID] = marquez_identity
            ramon_key = "ramon marquez"
            if name_index.get(ramon_key) == [OLD_UPID]:
                name_index[ramon_key] = [NEW_UPID]
            elif ramon_key in name_index:
                # Unexpected shape (e.g. multiple entries) -- don't guess,
                # just add the new key rather than mutating the old list.
                name_index[ramon_key] = list({*name_index[ramon_key], NEW_UPID} - {OLD_UPID}) or [NEW_UPID]
            else:
                name_index[ramon_key] = [NEW_UPID]
            upid_db["by_upid"] = by_upid
            upid_db["name_index"] = name_index
            changes.append((UPID_DB_PATH, upid_db))
    else:
        print(f"  upid_database.json: unexpected state (by_upid[{OLD_UPID}]={current_8697}, "
              f"by_upid[{NEW_UPID}]={current_8698}) -- ABORTING, no files touched.")
        sys.exit(1)

    # -------------------------------------------------------------------
    # 3. auction_current.json
    # -------------------------------------------------------------------
    auction = _load(AUCTION_PATH)
    bids = auction.get("bids", [])
    target_bid = next((b for b in bids if b.get("bid_id") == BID_ID), None)

    if target_bid is None:
        print(f"  auction_current.json: bid {BID_ID} not found -- assuming already resolved/removed  [SKIP]")
    elif target_bid.get("prospect_id") == NEW_UPID:
        print(f"  auction_current.json: bid {BID_ID} already points at {NEW_UPID}  [SKIP]")
    elif target_bid.get("prospect_id") == OLD_UPID:
        print(f"  auction_current.json: bid {BID_ID} prospect_id {OLD_UPID} -> {NEW_UPID}  [APPLY]")
        if not dry_run:
            target_bid["prospect_id"] = NEW_UPID
            changes.append((AUCTION_PATH, auction))
    else:
        print(f"  auction_current.json: bid {BID_ID} has unexpected prospect_id "
              f"{target_bid.get('prospect_id')!r} -- ABORTING, no files touched.")
        sys.exit(1)

    # -------------------------------------------------------------------
    # 4. player_log.json
    # -------------------------------------------------------------------
    player_log = _load(PLAYER_LOG_PATH)
    # Match on content, not a fully-formed id -- the real id embeds a
    # fresh timestamp on every run, so it can never equal a previously
    # written one. Look for any entry this script itself would have
    # written (same upid + update_type + tag suffix).
    already_logged = any(
        e.get("upid") == NEW_UPID
        and e.get("update_type") == "DataFix"
        and str(e.get("id", "")).endswith("UpidCollisionFix")
        for e in player_log
    )

    if already_logged:
        print(f"  player_log.json: correction entry already present  [SKIP]")
    else:
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        log_id = f"2026-{now_iso}-UPID_{NEW_UPID}-DataFix-UpidCollisionFix"
        entry = {
            "id": log_id,
            "season": 2026,
            "source": "manual_data_fix",
            "admin": "zpressley",
            "timestamp": now_iso,
            "upid": NEW_UPID,
            "player_name": "Ramon Marquez",
            "team": "SF",
            "pos": "SP",
            "age": 20,
            "level": "A",
            "team_rank": None,
            "rank": None,
            "eta": "",
            "player_type": "Farm",
            "owner": "",
            "contract": "",
            "status": "",
            "years": "P",
            "update_type": "DataFix",
            "event": (
                f"Data hygiene: Ramon Marquez was added via the admin tool on 2026-08-03 "
                f"and collided with Luis Garcia Jr.'s existing UPID {OLD_UPID} "
                f"(upid_database.json's by_upid was missing an {OLD_UPID} entry, so the "
                f"UPID generator didn't see the collision -- root cause fixed in "
                f"api_admin_bulk.py/api_manager_players.py/api_upid.py, see "
                f"api_upid.get_next_free_upid()). Reassigned Marquez to UPID {NEW_UPID}; "
                f"restored by_upid[{OLD_UPID}] to Luis Garcia Jr.; repointed the one open "
                f"auction bid (bid-7f3008e91865, $10 OB by WAR) from {OLD_UPID} to "
                f"{NEW_UPID}. Luis Garcia Jr.'s own combined_players.json record was never "
                f"altered by the collision or by this fix."
            ),
        }
        print(f"  player_log.json: appending correction entry (id={log_id})  [APPLY]")
        if not dry_run:
            player_log.append(entry)
            changes.append((PLAYER_LOG_PATH, player_log))

    # -------------------------------------------------------------------
    print(f"\n{'=' * 78}")
    if dry_run:
        print("Dry run — no files written. Re-run without --dry-run to apply.")
        return

    if not changes:
        print("Nothing to apply — already fully fixed.")
        return

    for path, data in changes:
        _save(path, data)
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
