"""Recover Luis Garcia Jr. (UPID 8696), lost to the add-player commit bug.

Background
----------
Zach added Luis Garcia Jr. (WSH, 1B) via the Website Admin Portal on
2026-07-30 around 3:59 PM ET. The bot posted a confirming "New Player
Added" Discord message (UPID: 8696, Admin: zpressley, Source: Website
Admin Portal) -- but no matching commit ever appears in git history, and
UPID 8696 does not exist in the live data/combined_players.json or
data/upid_database.json.

Root cause: api_admin_bulk.py's add_player() (and bulk_graduate /
bulk_update_contracts / bulk_release, which share the same helper) called
health.py's _commit_and_push() in fire-and-forget mode (wait=False, the
default) -- it queues the commit and returns success immediately without
confirming the git push actually happened. The Discord notification and
the API's "success" response both fire unconditionally before git does
anything. add_player() writes combined_players.json / upid_database.json /
player_log.json to local disk successfully (confirmed: this is a real bug
in the *commit* step, not the *save* step), but if the container redeploys
before the queued push lands -- which happens on every git push, and there
were several today -- the ephemeral filesystem resets to whatever was last
actually committed, silently erasing the add even though everyone had
already been told it succeeded. This is the same defect class already
fixed for trades (trade/trade_store.py:_maybe_commit); see the companion
fix in api_admin_bulk.py's _enqueue_commit() for the permanent fix so this
doesn't happen again for future adds.

This script recreates exactly what add_player() would have written, using
UPID 8696 (confirmed still free -- max UPID in both files is 8695, nothing
else has taken it since). Only name/team/position/player_type are known
with confidence (from the Discord confirmation + Zach's own description:
Nationals 1B). Bio fields Zach's form may have auto-filled via MLB Stats
API lookup (mlb_id, yahoo_id, birth_date, bats, throws, age) are NOT
guessed here -- this sandbox can't reach the MLB Stats API or Yahoo to
verify them, and writing a wrong mlb_id/yahoo_id into a live production
file is exactly the kind of mistake that caused the separate Luis
Garcia/Luis Garcia duplicate-UPID mess found during this same
investigation (UPIDs 2898/2899, both yahoo_id 9455). Those fields are left
blank/default, matching what add_player() would write if the form
submitted them blank -- flagged clearly below so Zach can fill them in
via the site's normal edit tools once this is live again.

Idempotent: guarded by an existence check on UPID 8696 in both files.

Run:
    python3 scripts/recover_lost_add_player_20260730.py --dry-run
    python3 scripts/recover_lost_add_player_20260730.py
"""

import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

COMBINED_FILE = "data/combined_players.json"
UPID_DB_FILE = "data/upid_database.json"
PLAYER_LOG_FILE = "data/player_log.json"

UPID = "8696"
ADMIN = "zpressley"

# Only the fields confirmed by the Discord notification + Zach's own
# description. Everything else is left blank, matching add_player()'s
# defaults for fields the submitted form didn't include.
NEW_PLAYER = {
    "upid": UPID,
    "name": "Luis García Jr.",
    "team": "WSH",
    "position": "1B",
    "age": None,
    "manager": "",
    "player_type": "MLB",
    "contract_type": "",
    "years_simple": "",
    "yahoo_id": "",
    "mlb_id": "",
    "birth_date": None,
    "debut_date": None,
    "bats": "",
    "throws": "",
    "fypd": False,
    "level": "",
}


def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def main():
    dry_run = "--dry-run" in sys.argv
    print(f"{'DRY RUN — ' if dry_run else ''}Recover UPID {UPID} (Luis García Jr.)\n" + "=" * 70)

    players = _load(COMBINED_FILE, [])
    upid_db = _load(UPID_DB_FILE, {"by_upid": {}, "name_index": {}})
    player_log = _load(PLAYER_LOG_FILE, [])

    if any(str(p.get("upid")) == UPID for p in players):
        print(f"SKIP: UPID {UPID} already exists in {COMBINED_FILE} — nothing to do.")
        return
    if UPID in (upid_db.get("by_upid") or {}):
        print(f"SKIP: UPID {UPID} already exists in {UPID_DB_FILE} — nothing to do.")
        return

    existing_upids = [int(p.get("upid")) for p in players if str(p.get("upid") or "").isdigit()]
    if existing_upids and max(existing_upids) >= int(UPID):
        print(f"ERROR: max UPID in {COMBINED_FILE} is {max(existing_upids)}, which is >= {UPID}. "
              "Reality has changed since diagnosis (something else may have taken this UPID). Aborting for manual review.")
        sys.exit(1)

    print(f"  Adding {NEW_PLAYER['name']} ({NEW_PLAYER['team']} {NEW_PLAYER['position']}) as UPID {UPID}  [APPLY]")
    print("  NOTE: mlb_id, yahoo_id, birth_date, bats, throws, age left blank —")
    print("        re-run the admin portal's player enrichment lookup (or edit manually)")
    print("        to fill these in once the fix is deployed.")

    if dry_run:
        print("\nDry run — no files written. Re-run without --dry-run to apply.")
        return

    players.append(dict(NEW_PLAYER))

    upid_db.setdefault("by_upid", {})
    upid_db.setdefault("name_index", {})
    upid_db["by_upid"][UPID] = {
        "upid": UPID,
        "name": NEW_PLAYER["name"],
        "team": NEW_PLAYER["team"],
        "pos": NEW_PLAYER["position"],
        "alt_names": [],
        "approved_dupes": "FALSE",
    }
    key = NEW_PLAYER["name"].lower().strip()
    upid_db["name_index"].setdefault(key, []).append(UPID)

    et = ZoneInfo("US/Eastern")
    ts = datetime.now(tz=et).isoformat()
    season = datetime.now().year
    log_entry = {
        "id": f"{season}-{ts}-UPID_{UPID}-Admin-Admin Portal",
        "season": season,
        "source": "Admin Portal",
        "admin": ADMIN,
        "timestamp": ts,
        "upid": UPID,
        "player_name": NEW_PLAYER["name"],
        "team": NEW_PLAYER["team"],
        "pos": NEW_PLAYER["position"],
        "age": NEW_PLAYER["age"],
        "level": "",
        "team_rank": None,
        "rank": None,
        "eta": "",
        "player_type": NEW_PLAYER["player_type"],
        "owner": "",
        "contract": "",
        "status": "",
        "years": "",
        "update_type": "Admin",
        "event": f"Player added to database by {ADMIN} (recovered — original add lost to commit bug, see api_admin_bulk.py fix)",
    }
    player_log.append(log_entry)

    # NOTE: data/combined_players.json's existing convention (from whatever
    # daily-pipeline code last wrote it) is ensure_ascii=True (escaped
    # unicode), unlike player_log.json/upid_database.json which are
    # unescaped. Match combined_players.json's existing style here so this
    # script doesn't flip ~300 unrelated names' encoding as a side effect
    # every time it (or something like it) runs — that already happened
    # once this session and had to be cleaned up by hand.
    with open(COMBINED_FILE, "w", encoding="utf-8") as f:
        json.dump(players, f, indent=2)
    with open(UPID_DB_FILE, "w", encoding="utf-8") as f:
        json.dump(upid_db, f, indent=2, ensure_ascii=False)
    with open(PLAYER_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(player_log, f, indent=2, ensure_ascii=False)

    print(f"\nWrote {COMBINED_FILE}, {UPID_DB_FILE}, {PLAYER_LOG_FILE}.")


if __name__ == "__main__":
    main()
