#!/usr/bin/env python3
"""Fix the UPID 8698 collision between Ramon Marquez and Boston Smith,
discovered 2026-08-04.

Sequence of events (reconstructed from player_log.json timestamps and git
history -- this is a second, independent occurrence of the same class of
bug as the 2026-08-03 Ramon Marquez / Luis Garcia Jr. collision, not a
repeat of that exact incident):

  1. 2026-08-03 15:07 ET -- Ramon Marquez added via admin tool, handed
     UPID 8697 (collided with Luis Garcia Jr. -- the original incident).
  2. 2026-08-03 16:28 ET -- Boston Smith added via admin tool (by a
     different admin), handed UPID 8698. At that moment this was NOT a
     collision: 8698 was genuinely free (Marquez was still sitting on
     8697; nothing else had ever used 8698).
  3. 2026-08-03 ~17:38 ET -- the fix for the Marquez/Garcia collision was
     applied (scripts/fix_upid_8697_collision_2026_08_03.py), moving
     Marquez from 8697 to 8698 -- which by then collided with Boston
     Smith, who'd taken 8698 an hour earlier. That script's guard only
     checked "is by_upid[8698] empty", not "does any combined_players.json
     row already hold 8698" -- Boston Smith's row was invisible to it
     (most likely because the sandbox that ran it had a local data clone
     that hadn't yet picked up Boston Smith's commit; both changes were
     legitimate on their own and only collided once reconciled together).

Net effect: combined_players.json ended up with two rows sharing UPID
8698 (Marquez, Boston Smith); upid_database.json's name_index still had
a stale "ramon marquez" -> ["8697"] entry (never updated to 8698) even
though by_upid["8698"] itself did correctly describe Marquez. The auction
board's two different UPID->name lookups (website vs. the Discord daily
summary bot) apparently resolve same-UPID collisions in opposite orders
(first-match vs. last-match), which is why one showed "Boston Smith" and
the other showed "Ramon Marquez" for the same WAR bid.

This script:
  1. data/combined_players.json -- reassigns Boston Smith's row from
     upid 8698 to upid 8699 (confirmed free in BOTH upid_database.json
     AND combined_players.json -- see check_truly_free() below; this is
     the fix api_upid.get_next_free_upid() should have been given the
     chance to make, had Boston Smith been added after that code fix
     went live). Ramon Marquez's row is left untouched.
  2. data/upid_database.json -- adds by_upid["8699"] for Boston Smith;
     unconditionally sets name_index["ramon marquez"] = ["8698"] and
     name_index["boston smith"] = ["8699"] (set directly rather than
     conditioned on whatever stale intermediate value is currently
     there -- yesterday's conditional approach is what let the
     "ramon marquez" entry's revert go unnoticed).
  3. data/auction_current.json -- NOT changed. The one open WAR bid's
     prospect_id is "8698", which will correctly and uniquely mean Ramon
     Marquez again once Boston Smith is off that UPID -- unlike
     yesterday, the bid's target UPID itself didn't change.
  4. data/player_log.json -- appends one new DataFix entry under UPID
     8699 documenting the reassignment. Boston Smith's original creation
     entry (UPID 8698, 2026-08-03 16:28 ET) is left as-is.

Guarded/idempotent: checks current state before mutating; re-running
after a successful apply is a safe no-op.

Run:
    python3 scripts/fix_upid_8698_collision_2026_08_04.py --dry-run
    python3 scripts/fix_upid_8698_collision_2026_08_04.py
"""

import json
import sys
from datetime import datetime, timezone

COMBINED_PATH = "data/combined_players.json"
UPID_DB_PATH = "data/upid_database.json"
PLAYER_LOG_PATH = "data/player_log.json"

OLD_UPID = "8698"
NEW_UPID = "8699"

BOSTON_SMITH_NAME = "Boston Smith"
BOSTON_SMITH_TEAM = "SEA"
MARQUEZ_MLB_ID = "827408"


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path, data):
    # combined_players.json is ensure_ascii=True on disk (unchanged since
    # 2026-08-03). upid_database.json and player_log.json have SINCE
    # flipped to ensure_ascii=False -- some other write path (not
    # api_admin_bulk.save_json, not api_upid._save_upid_db, not
    # pad_processor's log-append helper -- none of those pass
    # ensure_ascii explicitly, so all default to True; never tracked down
    # which path actually did it) rewrote both wholesale with raw UTF-8
    # between 2026-08-03's fix and this one. Matching each file's current
    # convention rather than assuming yesterday's still holds -- always
    # verify empirically (grep for '\\u00' vs raw accented chars) before
    # trusting a remembered convention for any of these files.
    ensure_ascii = path == COMBINED_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=ensure_ascii)


def _check_truly_free(new_upid, upid_db, players):
    """Cross-check against BOTH sources -- the exact thing yesterday's
    fix script skipped for its target UPID, which is how this second
    collision happened."""
    if new_upid in upid_db.get("by_upid", {}):
        return False, "already a key in upid_database.json's by_upid"
    if any(str(p.get("upid")) == new_upid for p in players):
        return False, "already held by a combined_players.json row"
    return True, ""


def main():
    dry_run = "--dry-run" in sys.argv
    label = "DRY RUN — " if dry_run else ""
    print(f"{label}Fix UPID {OLD_UPID} collision (Ramon Marquez / Boston Smith)\n" + "=" * 78)

    changes = []

    # -------------------------------------------------------------------
    # 1. combined_players.json
    # -------------------------------------------------------------------
    players = _load(COMBINED_PATH)
    rows_8698 = [p for p in players if str(p.get("upid")) == OLD_UPID]
    smith_row = next(
        (p for p in rows_8698 if p.get("name") == BOSTON_SMITH_NAME and p.get("team") == BOSTON_SMITH_TEAM),
        None,
    )
    marquez_row = next((p for p in rows_8698 if str(p.get("mlb_id")) == MARQUEZ_MLB_ID), None)

    already_fixed_combined = (
        smith_row is None
        and marquez_row is not None
        and len(rows_8698) == 1
        and any(p.get("name") == BOSTON_SMITH_NAME and str(p.get("upid")) == NEW_UPID for p in players)
    )

    if already_fixed_combined:
        print(f"  combined_players.json: already fixed (Smith at {NEW_UPID}, Marquez at {OLD_UPID})  [SKIP]")
    elif smith_row is not None and marquez_row is not None and len(rows_8698) == 2:
        ok, reason = _check_truly_free(NEW_UPID, _load(UPID_DB_PATH), players)
        if not ok:
            print(f"  combined_players.json: target UPID {NEW_UPID} is NOT actually free ({reason}) -- "
                  f"ABORTING, no files touched. Re-diagnose before re-running.")
            sys.exit(1)
        print(f"  combined_players.json: {len(rows_8698)} rows at upid {OLD_UPID} "
              f"(Marquez mlb_id={MARQUEZ_MLB_ID}, Smith name={BOSTON_SMITH_NAME!r}) "
              f"-- reassigning Boston Smith's row to upid {NEW_UPID} (confirmed free)  [APPLY]")
        if not dry_run:
            smith_row["upid"] = NEW_UPID
            changes.append((COMBINED_PATH, players))
    else:
        print(f"  combined_players.json: unexpected state ({len(rows_8698)} rows at {OLD_UPID}, "
              f"smith_row={'found' if smith_row else 'MISSING'}, "
              f"marquez_row={'found' if marquez_row else 'MISSING'}) -- ABORTING, no files touched.")
        sys.exit(1)

    # -------------------------------------------------------------------
    # 2. upid_database.json
    # -------------------------------------------------------------------
    upid_db = _load(UPID_DB_PATH)
    by_upid = upid_db.get("by_upid", {})
    name_index = upid_db.setdefault("name_index", {})

    if NEW_UPID in by_upid:
        print(f"  upid_database.json: by_upid[{NEW_UPID}] already present -- leaving it  [SKIP]")
    else:
        smith_identity = {
            "upid": NEW_UPID,
            "name": BOSTON_SMITH_NAME,
            "team": BOSTON_SMITH_TEAM,
            "pos": "C, OF",
            "alt_names": [],
            "approved_dupes": "FALSE",
        }
        print(f"  upid_database.json: adding by_upid[{NEW_UPID}] for Boston Smith  [APPLY]")
        if not dry_run:
            by_upid[NEW_UPID] = smith_identity

    # Set unconditionally -- don't branch on whatever stale value is
    # currently sitting there (that's exactly what let yesterday's
    # "ramon marquez" entry's revert go unnoticed).
    name_index_needs_fix = (
        name_index.get("ramon marquez") != [OLD_UPID]
        or name_index.get("boston smith") != [NEW_UPID]
    )
    if name_index_needs_fix:
        print(f"  upid_database.json: setting name_index['ramon marquez']=['{OLD_UPID}'], "
              f"name_index['boston smith']=['{NEW_UPID}']  [APPLY]")
        if not dry_run:
            name_index["ramon marquez"] = [OLD_UPID]
            name_index["boston smith"] = [NEW_UPID]
    else:
        print(f"  upid_database.json: name_index already correct  [SKIP]")

    if not dry_run and (NEW_UPID not in _load(UPID_DB_PATH).get("by_upid", {}) or name_index_needs_fix):
        upid_db["by_upid"] = by_upid
        upid_db["name_index"] = name_index
        changes.append((UPID_DB_PATH, upid_db))

    # -------------------------------------------------------------------
    # 3. auction_current.json -- intentionally not touched, see docstring.
    # -------------------------------------------------------------------
    print(f"  auction_current.json: not touched -- the open WAR bid's prospect_id ({OLD_UPID}) "
          f"correctly means Ramon Marquez once Boston Smith is off that UPID  [SKIP]")

    # -------------------------------------------------------------------
    # 4. player_log.json
    # -------------------------------------------------------------------
    player_log = _load(PLAYER_LOG_PATH)
    already_logged = any(
        e.get("upid") == NEW_UPID
        and e.get("update_type") == "DataFix"
        and str(e.get("id", "")).endswith("UpidCollisionFix2")
        for e in player_log
    )

    if already_logged:
        print(f"  player_log.json: correction entry already present  [SKIP]")
    else:
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        log_id = f"2026-{now_iso}-UPID_{NEW_UPID}-DataFix-UpidCollisionFix2"
        entry = {
            "id": log_id,
            "season": 2026,
            "source": "manual_data_fix",
            "admin": "zpressley",
            "timestamp": now_iso,
            "upid": NEW_UPID,
            "player_name": BOSTON_SMITH_NAME,
            "team": BOSTON_SMITH_TEAM,
            "pos": "C, OF",
            "age": 23,
            "level": "AA",
            "team_rank": None,
            "rank": None,
            "eta": "",
            "player_type": "Farm",
            "owner": "",
            "contract": "",
            "status": "",
            "years": "",
            "update_type": "DataFix",
            "event": (
                f"Data hygiene: Boston Smith was added via the admin tool on 2026-08-03 16:28 ET "
                f"and later collided with Ramon Marquez's UPID {OLD_UPID} when the previous day's "
                f"Marquez/Luis Garcia Jr. UPID-collision fix reassigned Marquez onto {OLD_UPID} without "
                f"visibility into Boston Smith's just-created row (see "
                f"scripts/fix_upid_8697_collision_2026_08_03.py). Reassigned Boston Smith to UPID "
                f"{NEW_UPID} (confirmed free against both upid_database.json and combined_players.json); "
                f"added by_upid[{NEW_UPID}]; corrected name_index for both 'ramon marquez' and "
                f"'boston smith'. The one open auction bid on {OLD_UPID} (WAR, $10 OB) was NOT changed -- "
                f"it now correctly and uniquely means Ramon Marquez again."
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
