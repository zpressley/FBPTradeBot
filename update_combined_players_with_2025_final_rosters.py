#!/usr/bin/env python3
"""Update data/combined_players.json with final 2025 rosters and export reports.

Inputs
------
- data/combined_players.json
    Master merged player view (Yahoo + sheet metadata).
- data/historical/2025/yahoo_players_2025.json
    Final 2025 rosters keyed by FBP team abbr (e.g., "WIZ", "B2J").

Behavior
--------
1) Build a map yahoo_id -> final_team_abbr from yahoo_players_2025.json.
2) For every record in combined_players.json that has a yahoo_id:
   - Update its `manager` to the final_team_abbr (or "" if unowned at EOY).
3) Write a timestamped backup of the original combined_players.json.
4) Write back the updated combined_players.json.
5) Generate a change-management CSV under data/historical/2025/ with
   change_type in {added, removed, kept}, comparing original vs final
   ownership per yahoo_id:
   - kept: old_owner == new_owner != ""
   - added: old_owner == "" and new_owner != ""
   - removed: old_owner != "" and new_owner == ""
   - transfers (old_owner != new_owner and both non-empty) are represented
     as two rows: one removed (old_owner) and one added (new_owner).
6) Generate an "owned players" CSV from the updated combined_players.json
   (records with non-empty `manager`).
"""

import copy
import csv
import json
import os
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.abspath(__file__))
COMBINED_PATH = os.path.join(ROOT, "data", "combined_players.json")
FINAL_2025_PATH = os.path.join(ROOT, "data", "historical", "2025", "yahoo_players_2025.json")
CHANGES_CSV_PATH = os.path.join(ROOT, "data", "historical", "2025", "combined_players_roster_changes_2025.csv")
OWNED_CSV_PATH = os.path.join(ROOT, "data", "historical", "2025", "combined_owned_players_2025.csv")
# Manager/team configuration for normalization (shared across repos).
MANAGERS_CONFIG_PATH = os.path.join(
    os.path.dirname(ROOT),  # parent dir containing fbp-hub
    "fbp-hub",
    "config",
    "managers.json",
)


def load_json(path: str):
    with open(path, "r") as f:
        return json.load(f)


def make_backup(path: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{path}.bak_{ts}"
    shutil.copy2(path, backup_path)
    return backup_path


def load_manager_teams() -> Dict[str, Dict[str, Any]]:
    """Load manager/team configuration from fbp-hub.

    Returns a dict keyed by FBP team abbreviation (e.g., "WIZ").
    """

    cfg = load_json(MANAGERS_CONFIG_PATH)
    teams = cfg.get("teams", {})
    # Normalize keys to upper-case abbreviations for consistency.
    return {abbr.strip().upper(): info for abbr, info in teams.items()}


def build_manager_key_index(teams: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """Build lookup from various manager strings -> FBP team abbr.

    Keys include:
    - FBP abbreviation (e.g., "WIZ")
    - Full team name (e.g., "Whiz Kids")
    - Manager/person name (e.g., "Zach")
    All keys are lowercased/stripped so lookups can be lenient.
    """

    index: Dict[str, str] = {}
    for abbr, info in teams.items():
        abbr_norm = abbr.strip().upper()
        index[abbr_norm.lower()] = abbr_norm

        name = (info.get("name") or "").strip()
        if name:
            index[name.lower()] = abbr_norm

        manager_person = (info.get("manager") or "").strip()
        if manager_person:
            index[manager_person.lower()] = abbr_norm

    return index


def normalize_manager_fields(
    records: List[Dict[str, Any]],
    teams: Dict[str, Dict[str, Any]],
    key_index: Dict[str, str],
) -> None:
    """Normalize `manager` + `FBP_Team` for all records in-place.

    After this runs, invariants:
    - `FBP_Team` is an FBP team abbreviation (e.g., "WIZ") or "".
    - `manager` is the full team name from managers.json (e.g., "Whiz Kids")
      or "" if unowned.
    """

    for rec in records:
        raw_mgr = (rec.get("manager") or "").strip()

        if not raw_mgr:
            rec["manager"] = ""
            rec["FBP_Team"] = ""
            continue

        # Try multiple interpretations of the existing value.
        candidates = [
            raw_mgr.strip().upper(),   # might be an abbreviation
            raw_mgr.strip().lower(),   # might be full name or person name
        ]

        abbr: Optional[str] = None
        for key in candidates:
            found = key_index.get(key.lower())
            if found:
                abbr = found
                break

        if not abbr:
            # Unknown owner string; preserve as-is, but do not invent FBP_Team.
            rec["FBP_Team"] = ""
            rec["manager"] = raw_mgr
            continue

        team_info = teams.get(abbr, {})
        full_name = (team_info.get("name") or raw_mgr).strip()

        rec["FBP_Team"] = abbr
        rec["manager"] = full_name


def build_final_owner_map(final_data: Dict[str, List[Dict[str, Any]]]) -> Dict[str, str]:
    """Return mapping yahoo_id -> final team abbr (e.g., "WIZ")."""

    owner_by_yahoo: Dict[str, str] = {}

    for team_abbr, players in final_data.items():
        for p in players:
            yid_raw = p.get("yahoo_id")
            if yid_raw is None:
                continue
            yid = str(yid_raw).strip()
            if not yid:
                continue

            if yid in owner_by_yahoo and owner_by_yahoo[yid] != team_abbr:
                # Log conflict but keep the first mapping for determinism.
                print(
                    f"⚠️ yahoo_id {yid} appears for multiple teams: "
                    f"{owner_by_yahoo[yid]} and {team_abbr}; keeping first",
                )
                continue

            owner_by_yahoo[yid] = team_abbr

    return owner_by_yahoo


def index_by_yahoo_id(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    idx: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        yid_raw = rec.get("yahoo_id")
        if yid_raw is None:
            continue
        yid = str(yid_raw).strip()
        if not yid:
            continue
        idx[yid] = rec
    return idx


def compute_changes(
    original_by_yid: Dict[str, Dict[str, Any]],
    updated_by_yid: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Compute change rows comparing original vs updated managers.

    Returns list of rows with:
    - change_type: added|removed|kept
    - yahoo_id, name, team, position, player_type
    - old_manager, new_manager
    """

    rows: List[Dict[str, Any]] = []

    all_yids = set(original_by_yid.keys()) | set(updated_by_yid.keys())

    for yid in sorted(all_yids, key=lambda x: int(x) if x.isdigit() else x):
        orig = original_by_yid.get(yid)
        new = updated_by_yid.get(yid)

        old_manager = (orig or {}).get("manager") or ""
        new_manager = (new or {}).get("manager") or ""

        # Only care about players that exist in the original combined list
        if orig is None:
            continue

        # Limit change report to MLB players; farm/prospect entries keep
        # their original owner and are not reported as changes.
        if (orig.get("player_type") or "") != "MLB":
            continue

        base_info = {
            "yahoo_id": yid,
            "name": orig.get("name"),
            "team": orig.get("team"),
            "position": orig.get("position"),
            "player_type": orig.get("player_type"),
            "old_manager": old_manager,
            "new_manager": new_manager,
        }

        if old_manager == new_manager:
            if old_manager:
                row = dict(base_info)
                row["change_type"] = "kept"
                rows.append(row)
            continue

        if not old_manager and new_manager:
            row = dict(base_info)
            row["change_type"] = "added"
            rows.append(row)
            continue

        if old_manager and not new_manager:
            row = dict(base_info)
            row["change_type"] = "removed"
            rows.append(row)
            continue

        if old_manager and new_manager and old_manager != new_manager:
            # Represent transfer as a removal (old owner) + addition (new owner).
            removed_row = dict(base_info)
            removed_row["change_type"] = "removed"
            rows.append(removed_row)

            added_row = dict(base_info)
            added_row["change_type"] = "added"
            rows.append(added_row)

    return rows


def write_changes_csv(rows: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(CHANGES_CSV_PATH), exist_ok=True)

    fieldnames = [
        "change_type",
        "yahoo_id",
        "name",
        "team",
        "position",
        "player_type",
        "old_manager",
        "new_manager",
    ]

    with open(CHANGES_CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_owned_csv(records: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(OWNED_CSV_PATH), exist_ok=True)

    fieldnames = [
        "FBP_Team",
        "manager",
        "name",
        "team",
        "position",
        "player_type",
        "contract_type",
        "status",
        "years_simple",
        "yahoo_id",
        "upid",
    ]

    owned_rows: List[Dict[str, Any]] = []
    for rec in records:
        manager = rec.get("manager") or ""
        if not manager:
            continue

        row = {k: rec.get(k) for k in fieldnames}
        owned_rows.append(row)

    with open(OWNED_CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(owned_rows)


def main() -> None:
    print("🔄 Updating combined_players.json with 2025 final rosters...")

    combined = load_json(COMBINED_PATH)
    final_2025 = load_json(FINAL_2025_PATH)

    # Load team/manager metadata and normalize current ownership fields
    manager_teams = load_manager_teams()
    manager_key_index = build_manager_key_index(manager_teams)
    normalize_manager_fields(combined, manager_teams, manager_key_index)

    owner_by_yahoo = build_final_owner_map(final_2025)

    # Deep copy for change comparison
    original_combined = copy.deepcopy(combined)

    # Index originals by yahoo_id
    original_by_yid = index_by_yahoo_id(original_combined)

    # Update managers in-place based on final owners
    updated_by_yid: Dict[str, Dict[str, Any]] = {}
    for rec in combined:
        yid_raw = rec.get("yahoo_id")
        if yid_raw is None:
            continue
        yid = str(yid_raw).strip()
        if not yid:
            continue

        # Only update MLB players; farm/prospect entries retain their
        # original manager even if they have a yahoo_id.
        player_type = rec.get("player_type") or ""
        if player_type != "MLB":
            updated_by_yid[yid] = rec
            continue

        # Determine final FBP team from 2025 rosters
        final_abbr = owner_by_yahoo.get(yid)
        if not final_abbr:
            # Unowned at end of season
            rec["FBP_Team"] = ""
            rec["manager"] = ""
        else:
            final_abbr = final_abbr.strip().upper()
            team_info = manager_teams.get(final_abbr, {})
            full_name = (team_info.get("name") or final_abbr).strip()
            rec["FBP_Team"] = final_abbr
            rec["manager"] = full_name

        updated_by_yid[yid] = rec

    backup_path = make_backup(COMBINED_PATH)
    print(f"📦 Backup created: {backup_path}")

    # Write updated combined_players.json
    with open(COMBINED_PATH, "w") as f:
        json.dump(combined, f, indent=2)

    # Change-management report
    change_rows = compute_changes(original_by_yid, updated_by_yid)
    write_changes_csv(change_rows)
    print(f"📄 Wrote change report to {CHANGES_CSV_PATH} ({len(change_rows)} rows)")

    # Owned players CSV from updated combined players
    write_owned_csv(combined)
    print(f"📄 Wrote owned players CSV to {OWNED_CSV_PATH}")

    print("✅ Update complete")


if __name__ == "__main__":
    main()
