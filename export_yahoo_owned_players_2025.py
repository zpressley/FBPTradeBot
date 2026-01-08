#!/usr/bin/env python3
"""Export 2025 Yahoo players who were owned at season end to CSV.

Inputs:
- data/historical/2025/team_mappings_2025.json
    * Maps Yahoo team IDs -> team_name/manager/fbp_abbr
- data/yahoo_all_players_2025.json
    * Full snapshot of all Yahoo players, with `owned_by` field.

Logic:
- Consider only players with `ownership_type == "team"` and non-null `owned_by`.
- Extract Yahoo team ID from the `owned_by` string; e.g. "458.l.15505.t.2" -> "2".
- Join against team_mappings_2025 on `yahoo_team_id`.
- Write a CSV of owned players under data/historical/2025/.
"""

import csv
import json
import os
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.abspath(__file__))
TEAM_MAP_PATH = os.path.join(ROOT, "data", "historical", "2025", "team_mappings_2025.json")
PLAYERS_PATH = os.path.join(ROOT, "data", "yahoo_all_players_2025.json")
OUTPUT_PATH = os.path.join(ROOT, "data", "historical", "2025", "yahoo_owned_players_2025.csv")


def load_team_mappings() -> Dict[str, Dict[str, Any]]:
    with open(TEAM_MAP_PATH, "r") as f:
        data: List[Dict[str, Any]] = json.load(f)
    return {str(entry["yahoo_team_id"]): entry for entry in data}


def load_players() -> List[Dict[str, Any]]:
    with open(PLAYERS_PATH, "r") as f:
        return json.load(f)


def extract_team_id(owned_by: str) -> str:
    """Extract Yahoo team ID from an owned_by string.

    Examples:
    - "458.l.15505.t.2"   -> "2"
    - "458.l.15505.t.10"  -> "10"
    Fallback: last segment after '.' if pattern is different.
    """

    if ".t." in owned_by:
        return owned_by.split(".t.")[-1]
    # Fallback: last token
    return owned_by.split(".")[-1]


def main() -> None:
    print("📤 Exporting 2025 owned Yahoo players to CSV...")

    team_map = load_team_mappings()
    players = load_players()

    owned_rows: List[Dict[str, Any]] = []

    for p in players:
        ownership_type = p.get("ownership_type")
        owned_by = p.get("owned_by")

        # Only players owned by a team in the final snapshot
        if ownership_type != "team" or not owned_by:
            continue

        team_id = extract_team_id(str(owned_by))
        mapping = team_map.get(str(team_id))

        row: Dict[str, Any] = {}
        row["yahoo_team_id"] = team_id
        row["team_name"] = mapping.get("team_name") if mapping else ""
        row["manager_name"] = mapping.get("manager_name") if mapping else ""
        row["fbp_abbr"] = mapping.get("fbp_abbr") if mapping else ""

        row["player_id"] = p.get("player_id")
        row["player_key"] = p.get("player_key")
        row["name"] = p.get("name")
        row["first_name"] = p.get("first_name")
        row["last_name"] = p.get("last_name")
        row["position"] = p.get("position")
        row["team"] = p.get("team")
        row["team_full"] = p.get("team_full")
        row["status"] = p.get("status")
        row["ownership_type"] = ownership_type
        row["owned_by"] = owned_by
        row["percent_owned"] = p.get("percent_owned")

        # Optionally include eligible_positions as a semi-colon string
        elig = p.get("eligible_positions") or []
        if isinstance(elig, list):
            row["eligible_positions"] = ";".join(str(e) for e in elig)
        else:
            row["eligible_positions"] = str(elig)

        owned_rows.append(row)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    fieldnames = [
        "yahoo_team_id",
        "team_name",
        "manager_name",
        "fbp_abbr",
        "player_id",
        "player_key",
        "name",
        "first_name",
        "last_name",
        "position",
        "eligible_positions",
        "team",
        "team_full",
        "status",
        "ownership_type",
        "owned_by",
        "percent_owned",
    ]

    with open(OUTPUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(owned_rows)

    print(f"✅ Wrote {len(owned_rows)} owned players to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
