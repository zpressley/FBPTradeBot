#!/usr/bin/env python3
"""Export a CSV of all Farm players from combined_players.json.

Output:
- data/historical/2026/farm_players_2026.csv

Columns (per row):
- upid, name, manager, team, FBP_Team, player_type, contract_type,
  status, years_simple, position, mlb_primary_position, mlb_id,
  yahoo_id, debuted, age
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
COMBINED_PATH = DATA_DIR / "combined_players.json"
OUT_DIR = DATA_DIR / "historical" / "2026"
OUT_CSV = OUT_DIR / "farm_players_2026.csv"


def load_combined() -> List[Dict[str, Any]]:
    if not COMBINED_PATH.exists():
        raise SystemExit(f"ERROR: {COMBINED_PATH} not found")
    with COMBINED_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit("combined_players.json did not contain a JSON array")
    return data


def export_farm_players() -> None:
    players = load_combined()

    farm_players: List[Dict[str, Any]] = []
    for p in players:
        pt = (p.get("player_type") or "").strip()
        if pt != "Farm":
            continue
        farm_players.append(p)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "upid",
        "name",
        "manager",
        "team",
        "FBP_Team",
        "player_type",
        "contract_type",
        "status",
        "years_simple",
        "position",
        "mlb_primary_position",
        "mlb_id",
        "yahoo_id",
        "debuted",
        "age",
    ]

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for p in farm_players:
            row = {
                "upid": p.get("upid") or "",
                "name": p.get("name") or "",
                "manager": p.get("manager") or "",
                "team": p.get("team") or "",
                "FBP_Team": p.get("FBP_Team") or "",
                "player_type": p.get("player_type") or "",
                "contract_type": p.get("contract_type") or "",
                "status": p.get("status") or "",
                "years_simple": p.get("years_simple") or "",
                "position": p.get("position") or "",
                "mlb_primary_position": p.get("mlb_primary_position") or "",
                "mlb_id": p.get("mlb_id") or "",
                "yahoo_id": p.get("yahoo_id") or "",
                "debuted": p.get("debuted"),
                "age": p.get("age"),
            }
            writer.writerow(row)

    print(f"Exported {len(farm_players)} Farm players to {OUT_CSV}")


if __name__ == "__main__":
    export_farm_players()
