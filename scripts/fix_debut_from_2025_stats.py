#!/usr/bin/env python3
"""One-off fixer: mark Farm players as debuted based solely on 2025 Yahoo stats.

Context
-------
The full debut pipeline in data_pipeline/add_debut_flags.py does two things:
- pulls MLB debut dates from the MLB Stats API via ProspectStatsRepository
- ORs in a "debuted" flag based on 2025 Yahoo MLB stats

That script has errored in this environment and we do NOT want to re-run the
full MLB API flow. Instead, this utility only looks at 2025 Yahoo stats and
updates data/combined_players.json by setting:

    p["debuted"] = True

for any Farm player who:
- has a yahoo_id that appears in data/stats/yahoo_players_2025_stats.csv, and
- recorded any AB (from H/AB) or IP in 2025.

We do not modify debut_date and we do not clear existing debuted flags; we
simply OR in additional True values for players who clearly appeared in MLB in
2025.

Safety notes
------------
- Input:  data/combined_players.json, data/stats/yahoo_players_2025_stats.csv
- Output: backup at data/combined_players_backup_debut_from_2025_stats.json
          updated data/combined_players.json written in-place

This script is idempotent: running it again will yield the same result.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, Set

import sys

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
COMBINED_PATH = DATA_DIR / "combined_players.json"
YAHOO_STATS_2025 = DATA_DIR / "stats" / "yahoo_players_2025_stats.csv"
YAHOO_WITH_UPID_2026 = DATA_DIR / "historical" / "2026" / "yahoo_all_players_2026_with_upid.csv"


def load_farm_by_upid(players) -> Dict[str, dict]:
    """Return mapping upid -> player dict for Farm players only."""
    by_upid: Dict[str, dict] = {}
    for p in players:
        if (p.get("player_type") or "").strip() != "Farm":
            continue
        upid = str(p.get("upid") or "").strip()
        if not upid:
            continue
        by_upid[upid] = p
    return by_upid


def load_upid_to_yahoo_id() -> Dict[str, str]:
    """Load mapping upid -> yahoo_player_id from the 2026 export CSV.

    This lets us bridge from UPID (used in combined_players.json) to the
    numeric yahoo player ids used in the 2025 stats CSV, without requiring
    yahoo_id to already be present on Farm records.
    """

    mapping: Dict[str, str] = {}
    if not YAHOO_WITH_UPID_2026.exists():
        print(f"⚠️  yahoo_all_players_2026_with_upid.csv not found at {YAHOO_WITH_UPID_2026}")
        return mapping

    with YAHOO_WITH_UPID_2026.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            upid = (row.get("upid") or "").strip()
            yahoo_id = (row.get("yahoo_player_id") or "").strip()
            if not upid or not yahoo_id:
                continue
            mapping[upid] = yahoo_id

    print(f"✅ Loaded UPID→Yahoo mapping for {len(mapping)} players from 2026 export")
    return mapping


def load_yahoo_debut_flags(limit_to_ids: Set[str]) -> Dict[str, bool]:
    """Return mapping yahoo_id -> True if player had AB or IP in 2025.

    This is a trimmed-down version of load_yahoo_debut_flags() from
    data_pipeline/add_debut_flags.py, with two key differences:
    - it only cares about player_ids present in limit_to_ids (Farm players)
    - it does NOT talk to MLB APIs; CSV-only.
    """

    flags: Dict[str, bool] = {}
    if not YAHOO_STATS_2025.exists():
        print(f"⚠️  Yahoo stats CSV for 2025 not found at {YAHOO_STATS_2025}")
        return flags

    with YAHOO_STATS_2025.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            player_id = (row.get("player_id") or "").strip()
            if not player_id or player_id not in limit_to_ids:
                continue

            hab = (row.get("H/AB") or "").strip()
            ip_str = (row.get("IP") or "").strip()

            has_ab = False
            has_ip = False

            # Hitters: H/AB like "114/477"; we only care about AB.
            if hab and "/" in hab:
                try:
                    _h_s, ab_s = hab.split("/", 1)
                    ab = int(ab_s)
                    has_ab = ab > 0
                except ValueError:
                    # "-/-" or malformed; ignore
                    pass

            if ip_str:
                try:
                    ip_val = float(ip_str)
                    has_ip = ip_val > 0.0
                except ValueError:
                    pass

            if has_ab or has_ip:
                flags[player_id] = True

    print(f"✅ Loaded 2025 Yahoo debut flags for {len(flags)} Farm players")
    return flags


def main() -> None:
    if not COMBINED_PATH.exists():
        raise SystemExit(f"combined_players.json not found at {COMBINED_PATH}")

    with COMBINED_PATH.open("r", encoding="utf-8") as f:
        players = json.load(f)

    print(f"📄 Loaded {len(players)} players from combined_players.json")

    farm_by_upid = load_farm_by_upid(players)
    print(f"📊 Found {len(farm_by_upid)} Farm players with UPID")

    upid_to_yahoo = load_upid_to_yahoo_id()
    yahoo_ids_for_farm: Set[str] = set()
    for upid in farm_by_upid.keys():
        yahoo_id = upid_to_yahoo.get(upid)
        if yahoo_id:
            yahoo_ids_for_farm.add(yahoo_id)

    print(f"📊 Mapped {len(yahoo_ids_for_farm)} Farm players to Yahoo IDs via 2026 export")

    yahoo_debut = load_yahoo_debut_flags(yahoo_ids_for_farm)

    updated = 0
    for upid, player in farm_by_upid.items():
        yahoo_id = upid_to_yahoo.get(upid)
        if not yahoo_id:
            continue
        if not yahoo_debut.get(yahoo_id):
            continue

        # Only OR in the flag; do not unset if already true.
        if not player.get("debuted"):
            player["debuted"] = True
            updated += 1

    print(f"✅ Marked {updated} additional Farm players as debuted based on 2025 stats")

    backup_path = COMBINED_PATH.with_name("combined_players_backup_debut_from_2025_stats.json")
    with backup_path.open("w", encoding="utf-8") as bf:
        json.dump(players, bf, indent=2)
    print(f"📦 Backup written to {backup_path}")

    with COMBINED_PATH.open("w", encoding="utf-8") as f:
        json.dump(players, f, indent=2)
    print(f"💾 Saved updated combined_players.json with additional debuted flags.")


if __name__ == "__main__":
    main()
