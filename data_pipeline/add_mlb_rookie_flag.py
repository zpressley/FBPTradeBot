#!/usr/bin/env python3
"""Add MLBRookie flag to data/combined_players.json based on MLB AB/IP.

Rules (approximate, per FBP Constitution 2026):
- Hitters lose rookie status if they reach **130+ career MLB AB**.
- Pitchers lose rookie status if they reach **50+ career MLB IP**.
- We currently approximate "career" using 2024 + 2025 MLB stats from Yahoo.

Inputs:
- data/combined_players.json  (will be updated in-place, backup created)
- data/yahoo_players_2025_stats.csv
- data/yahoo_players_2024_stats.csv (optional; if present, included in totals)

Mapping:
- We key off `yahoo_id` in combined_players and `player_id` in the Yahoo CSVs.

Edge cases:
- If we cannot find any MLB stats for a player in either CSV, we treat them as
  **still rookie-eligible** (MLBRookie = True).
"""

import csv
import json
import os
from typing import Dict, Tuple

ROOT = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(ROOT, "data")
COMBINED_PATH = os.path.join(DATA_DIR, "combined_players.json")

YAHOO_YEARS = [2024, 2025]

HITTER_AB_THRESHOLD = 130
PITCHER_IP_THRESHOLD = 50.0


def load_yahoo_stats() -> Dict[str, Tuple[int, float]]:
    """Load Yahoo MLB stats for the configured years.

    Returns a mapping: yahoo_id (str) -> (career_ab, career_ip).
    """

    stats: Dict[str, Tuple[int, float]] = {}

    for year in YAHOO_YEARS:
        path = os.path.join(DATA_DIR, f"yahoo_players_{year}_stats.csv")
        if not os.path.exists(path):
            print(f"⚠️  Yahoo stats CSV not found for {year}: {path} (skipping year)")
            continue

        print(f"📄 Loading Yahoo stats for {year} from {path}...")
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                player_id = row.get("player_id")
                if not player_id:
                    continue

                # Initialize
                ab_total, ip_total = stats.get(player_id, (0, 0.0))

                # Determine if this row is hitter vs pitcher based on presence of
                # H/AB (hitters) vs IP (pitchers). Some rows have both halves
                # populated in the CSV, but each row should correspond to one
                # role for our purposes.
                hab = (row.get("H/AB") or "").strip()
                ip_str = (row.get("IP") or "").strip()

                # Hitters: H/AB like "114/477"; we only care about AB.
                if hab and "/" in hab:
                    try:
                        _, ab_s = hab.split("/", 1)
                        ab = int(ab_s)
                        ab_total += ab
                    except ValueError:
                        # "-/-" or malformed; ignore
                        pass

                # Pitchers: IP as a decimal innings value (e.g. 52.1, 150.0).
                if ip_str:
                    try:
                        ip = float(ip_str)
                        ip_total += ip
                    except ValueError:
                        pass

                stats[player_id] = (ab_total, ip_total)

        print(f"   ✅ Aggregated stats for {len(stats)} players so far")

    return stats


def classify_rookie(ab: int, ip: float) -> bool:
    """Return True if player is still rookie-eligible, False otherwise."""
    # If we have *no* MLB stats at all, treat as rookie.
    if ab == 0 and ip == 0.0:
        return True

    # If they exceed either threshold, they are no longer a rookie.
    if ab >= HITTER_AB_THRESHOLD or ip >= PITCHER_IP_THRESHOLD:
        return False

    return True


def add_mlb_rookie_flag() -> None:
    # 1. Load combined players
    print(f"📄 Loading combined players from {COMBINED_PATH}...")
    with open(COMBINED_PATH, "r") as f:
        players = json.load(f)

    print(f"   ✅ Loaded {len(players)} players")

    # 2. Load Yahoo stats map
    stats_map = load_yahoo_stats()
    print(f"📊 Stats map contains {len(stats_map)} players with MLB stats")

    # 3. Compute MLBRookie for each player
    updated = 0
    no_yahoo_id = 0

    for p in players:
        yahoo_id = str(p.get("yahoo_id") or "").strip()

        # Default: rookie unless stats show otherwise.
        ab_total = 0
        ip_total = 0.0

        if yahoo_id and yahoo_id in stats_map:
            ab_total, ip_total = stats_map[yahoo_id]
        elif yahoo_id:
            # Has a Yahoo ID but we didn't see stats; keep as rookie, but count.
            no_yahoo_id += 1

        mlb_rookie = classify_rookie(ab_total, ip_total)

        # League rule: anyone classified as an MLB player in combined_players
        # is NOT rookie-eligible, regardless of AB/IP thresholds.
        player_type = (p.get("player_type") or "").strip()
        if player_type == "MLB":
            mlb_rookie = False

        if p.get("MLBRookie") != mlb_rookie:
            p["MLBRookie"] = mlb_rookie
            updated += 1

    print(f"✅ Updated MLBRookie flag on {updated} players")
    if no_yahoo_id:
        print(f"ℹ️  {no_yahoo_id} players had yahoo_id but no matching stats in CSVs")

    # 4. Backup and save
    backup_path = COMBINED_PATH.replace(".json", "_backup_mlb_rookie.json")
    with open(backup_path, "w") as bf:
        json.dump(players, bf, indent=2)
    print(f"📦 Backup written to {backup_path}")

    with open(COMBINED_PATH, "w") as f:
        json.dump(players, f, indent=2)
    print(f"💾 Saved updated combined players with MLBRookie flag to {COMBINED_PATH}")


if __name__ == "__main__":
    add_mlb_rookie_flag()
