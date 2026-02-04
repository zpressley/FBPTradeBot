#!/usr/bin/env python3
"""Add MLB debut metadata to Farm players in combined_players.json.

For each player with player_type == "Farm":
- Fetch MLB debut date from the MLB Stats API (via ProspectStatsRepository)
  using mlb_id.
- Look up 2025 Yahoo MLB stats; if the player recorded any AB or IP in 2025,
  they are considered to have debuted for FBP rules.

We then write back to data/combined_players.json with two new fields:
- debut_date: MM/DD/YYYY string or null
- debuted: boolean (True if we believe they have debuted in MLB as of 2025)

This script is intentionally idempotent; running it again will refresh
these fields based on the latest MLB/Yahoo data and cache.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prospect_stats_repository import ProspectStatsRepository
DATA_DIR = ROOT / "data"
COMBINED_PATH = DATA_DIR / "combined_players.json"
YAHOO_STATS_2025 = DATA_DIR / "yahoo_players_2025_stats.csv"

DEBUT_CUTOFF_SEASON = 2025


def load_yahoo_debut_flags() -> Dict[str, bool]:
    """Return mapping yahoo_id -> True if player had AB or IP in 2025.

    We reuse the same CSV structure used by add_mlb_rookie_flag:
    - H/AB column for hitters (we only care about AB)
    - IP column for pitchers
    """

    flags: Dict[str, bool] = {}
    if not YAHOO_STATS_2025.exists():
        print(f"⚠️ Yahoo stats CSV for 2025 not found at {YAHOO_STATS_2025}")
        return flags

    with YAHOO_STATS_2025.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            player_id = (row.get("player_id") or "").strip()
            if not player_id:
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
                    pass

            if ip_str:
                try:
                    ip_val = float(ip_str)
                    has_ip = ip_val > 0.0
                except ValueError:
                    pass

            if has_ab or has_ip:
                flags[player_id] = True

    print(f"✅ Loaded 2025 Yahoo debut flags for {len(flags)} players")
    return flags


def main() -> None:
    if not COMBINED_PATH.exists():
        raise SystemExit(f"combined_players.json not found at {COMBINED_PATH}")

    with COMBINED_PATH.open("r", encoding="utf-8") as f:
        players = json.load(f)

    print(f"📄 Loaded {len(players)} players from combined_players.json")

    yahoo_debut = load_yahoo_debut_flags()

    repo = ProspectStatsRepository()
    mlb_debut_cache: Dict[int, str | None] = {}

    updated = 0
    missing_mlb_id = 0

    for p in players:
        if (p.get("player_type") or "").strip() != "Farm":
            continue

        # Determine Yahoo-based debut flag for 2025.
        yahoo_id = str(p.get("yahoo_id") or "").strip()
        has_2025_stats = bool(yahoo_id and yahoo_debut.get(yahoo_id))

        # Determine MLB debut date via MLB API when mlb_id is present.
        mlb_id = p.get("mlb_id")
        debut_iso: str | None = None

        if isinstance(mlb_id, int):
            if mlb_id in mlb_debut_cache:
                debut_iso = mlb_debut_cache[mlb_id]
            else:
                stats = repo.fetch_player_stats(mlb_id, p.get("name", ""))
                debut_iso = stats.get("mlb_debut") if stats else None  # type: ignore[assignment]
                mlb_debut_cache[mlb_id] = debut_iso
        else:
            missing_mlb_id += 1

        debut_date_str: str | None = None
        debuted_flag = False

        if debut_iso:
            try:
                dt = datetime.fromisoformat(debut_iso)
                debut_date_str = dt.strftime("%m/%d/%Y")
                debuted_flag = dt.year <= DEBUT_CUTOFF_SEASON
            except Exception:
                pass

        # Union condition: any MLB debut on/before 2025 OR Yahoo 2025 stats.
        if has_2025_stats:
            debuted_flag = True

        # Only write fields when we have *some* signal; otherwise leave
        # previously-set values intact if they exist.
        if debut_date_str is not None:
            if p.get("debut_date") != debut_date_str:
                p["debut_date"] = debut_date_str
                updated += 1
        else:
            # Ensure key exists but null for UI convenience.
            if "debut_date" not in p:
                p["debut_date"] = None

        if p.get("debuted") != debuted_flag:
            p["debuted"] = debuted_flag
            updated += 1

    backup_path = COMBINED_PATH.with_name("combined_players_backup_with_debut.json")
    with backup_path.open("w", encoding="utf-8") as bf:
        json.dump(players, bf, indent=2)
    print(f"📦 Backup written to {backup_path}")

    with COMBINED_PATH.open("w", encoding="utf-8") as f:
        json.dump(players, f, indent=2)
    print(f"💾 Saved updated combined_players.json with debut_date / debuted flags ({updated} changes).")

    if missing_mlb_id:
        print(f"ℹ️ {missing_mlb_id} Farm players lacked mlb_id and were updated using Yahoo-only data.")


if __name__ == "__main__":
    main()
