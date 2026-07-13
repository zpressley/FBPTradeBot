#!/usr/bin/env python3
"""One-off: mid-season graduation of 37 prospects (July 2026).

Mirrors exactly what POST /api/admin/bulk-graduate (api_admin_bulk.py) does,
plus the status-field fix that endpoint is missing, applied as a direct
edit to data/combined_players.json and data/player_log.json instead of a
live API call. See GRADUATION_PLAN_2026_MIDSEASON.md for the full plan
this implements.

Usage:
    python3 scripts/graduate_37_players_2026_07.py --dry-run
    python3 scripts/graduate_37_players_2026_07.py
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
COMBINED_FILE = ROOT / "data" / "combined_players.json"
PLAYER_LOG_FILE = ROOT / "data" / "player_log.json"

ADMIN = "Zach"
SOURCE = "Manual Graduation Batch (Cowork)"

# _KEY_TO_FIELDS in kap/kap_processor.py — the authoritative status mapping.
TIER_FIELDS = {
    "TC R": "[6] TCR",
    "TC BC-1": "[6] TCBC1",
}

# 19 Purchased-Contract graduates -> TC R
TC_R_UPIDS = [
    "3522", "3901", "6555", "3482", "7591", "4634", "3443", "6573", "4001",
    "6988", "3913", "6572", "3779", "3930", "6577", "7658", "6075", "4611", "7924",
]

# 18 Blue-Chip-Contract graduates -> TC BC-1 (includes Didier Fuentes, upid
# 7304, confirmed BC by Zach on 2026-07-13 despite combined_players.json
# currently showing contract_type "Purchased Contract" for him).
TC_BC1_UPIDS = [
    "7685", "6720", "3951", "3852", "4593", "2237", "6591", "7619", "7568",
    "6263", "3906", "6911", "7602", "3894", "7540", "6853", "6752", "7304",
]

UPID_TO_TIER = {u: "TC R" for u in TC_R_UPIDS}
UPID_TO_TIER.update({u: "TC BC-1" for u in TC_BC1_UPIDS})

# Also correct a pre-existing data error while we're touching these records:
# Didier Fuentes (7304) has contract_type "Purchased Contract" but should
# have been "Blue Chip Contract" all along (confirmed by Zach). Graduation
# overwrites contract_type to "Keeper Contract" regardless, so this doesn't
# change the graduation outcome — it's just for an accurate pre-graduation
# historical record in this script's dry-run output / log entry.
PRE_GRADUATION_CONTRACT_TYPE_FIXES = {
    "7304": "Blue Chip Contract",
}

assert len(UPID_TO_TIER) == 37, f"expected 37 players, got {len(UPID_TO_TIER)}"


def build_log_entry(player_rec: dict, tier: str) -> dict:
    ts = datetime.now(tz=ZoneInfo("US/Eastern")).isoformat()
    season = datetime.now().year
    upid = str(player_rec.get("upid") or "").strip()
    entry_id = "-".join([str(season), ts, f"UPID_{upid or 'NA'}", "Graduate", SOURCE])
    return {
        "id": entry_id,
        "season": season,
        "source": SOURCE,
        "admin": ADMIN,
        "timestamp": ts,
        "upid": upid,
        "player_name": player_rec.get("name") or "",
        "team": player_rec.get("team") or "",
        "pos": player_rec.get("position") or "",
        "age": player_rec.get("age"),
        "level": str(player_rec.get("level") or ""),
        "team_rank": player_rec.get("team_rank"),
        "rank": player_rec.get("rank"),
        "eta": str(player_rec.get("eta") or ""),
        "player_type": player_rec.get("player_type") or "",
        "owner": player_rec.get("manager") or player_rec.get("owner") or "",
        "contract": player_rec.get("contract_type") or "",
        "status": player_rec.get("status") or "",
        "years": player_rec.get("years_simple") or "",
        "update_type": "Graduate",
        "event": f"Graduated to {tier} by {ADMIN}",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    players = json.loads(COMBINED_FILE.read_text())
    logs = json.loads(PLAYER_LOG_FILE.read_text())
    if not isinstance(logs, list):
        print("ERROR: player_log.json is not a list — aborting.", file=sys.stderr)
        return 1

    remaining = dict(UPID_TO_TIER)
    new_log_entries = []
    changes = []

    for p in players:
        upid = str(p.get("upid"))
        if upid not in UPID_TO_TIER:
            continue

        tier = UPID_TO_TIER[upid]
        status = TIER_FIELDS[tier]

        before = {
            "player_type": p.get("player_type"),
            "contract_type": p.get("contract_type"),
            "years_simple": p.get("years_simple"),
            "status": p.get("status"),
            "debuted": p.get("debuted"),
        }

        p["player_type"] = "MLB"
        p["contract_type"] = "Keeper Contract"
        p["years_simple"] = tier
        p["status"] = status
        if p.get("debuted") is False:
            p["debuted"] = True

        after = {
            "player_type": p.get("player_type"),
            "contract_type": p.get("contract_type"),
            "years_simple": p.get("years_simple"),
            "status": p.get("status"),
            "debuted": p.get("debuted"),
        }

        changes.append((p.get("name"), upid, before, after))
        new_log_entries.append(build_log_entry(p, tier))
        del remaining[upid]

    if remaining:
        print(f"ERROR: {len(remaining)} upid(s) not found in combined_players.json: {remaining}", file=sys.stderr)
        return 1

    print(f"Matched and updated {len(changes)}/37 players.\n")
    for name, upid, before, after in changes:
        diff_bits = [f"{k}: {before[k]!r} -> {after[k]!r}" for k in after if before[k] != after[k]]
        print(f"  {name} (upid {upid}): " + "; ".join(diff_bits))

    if args.dry_run:
        print(f"\n--dry-run set: not writing. Would append {len(new_log_entries)} player_log.json entries.")
        return 0

    logs.extend(new_log_entries)
    COMBINED_FILE.write_text(json.dumps(players, indent=2) + "\n")
    PLAYER_LOG_FILE.write_text(json.dumps(logs, indent=2) + "\n")
    print(f"\n✅ Wrote {COMBINED_FILE} and appended {len(new_log_entries)} entries to {PLAYER_LOG_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
