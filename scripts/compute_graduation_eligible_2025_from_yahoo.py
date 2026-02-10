#!/usr/bin/env python3
"""Compute 2025 graduation-eligible prospects from Yahoo stats.

This script:
- Reads data/combined_players.json
- Reads data/historical/2026/yahoo_all_players_2026_with_upid.csv
- Reads data/stats/yahoo_players_2025_stats.csv
- For Farm players who have debuted (debuted == True) and have a Yahoo stats
  line in 2025, it checks FBP-style graduation thresholds:

  * Age-based: age >= 26 (using combined_players["age"] when available)
  * Batters: AB >= 350 OR APP >= 80
  * Pitchers: IP >= 100 OR APP >= 30

- Writes a CSV report listing all Farm players that meet any of these
  thresholds, without changing contracts or player_type.

Output:
- data/historical/2026/graduation_eligible_2025_from_yahoo.csv

This is a read-only helper for manual review / downstream scripts.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

COMBINED_PATH = DATA_DIR / "combined_players.json"
YAHOO_WITH_UPID_2026 = DATA_DIR / "historical" / "2026" / "yahoo_all_players_2026_with_upid.csv"
YAHOO_STATS_2025 = DATA_DIR / "stats" / "yahoo_players_2025_stats.csv"
OUT_DIR = DATA_DIR / "historical" / "2026"
OUT_CSV = OUT_DIR / "graduation_eligible_2025_from_yahoo.csv"

AGE_LIMIT = 25  # FBP: age 25 & under are prospects; turn 26 -> auto graduate
BATTER_AB_LIMIT = 350
BATTER_APP_LIMIT = 80
PITCHER_IP_LIMIT = 100.0
PITCHER_APP_LIMIT = 30


def load_combined_players() -> list[dict]:
    if not COMBINED_PATH.exists():
        raise SystemExit(f"ERROR: {COMBINED_PATH} not found")
    with COMBINED_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit("combined_players.json did not contain a JSON array")
    return data


def load_upid_to_yahoo_id() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    if not YAHOO_WITH_UPID_2026.exists():
        print(f"WARN: {YAHOO_WITH_UPID_2026} not found; graduation report will be partial")
        return mapping

    with YAHOO_WITH_UPID_2026.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            upid = (row.get("upid") or "").strip()
            yid = (row.get("yahoo_player_id") or "").strip()
            if not upid or not yid:
                continue
            mapping[upid] = yid

    print(f"Loaded UPID→Yahoo mapping for {len(mapping)} players")
    return mapping


def load_yahoo_stats_2025() -> Dict[str, dict]:
    stats: Dict[str, dict] = {}
    if not YAHOO_STATS_2025.exists():
        print(f"WARN: {YAHOO_STATS_2025} not found; no graduation report will be produced")
        return stats

    with YAHOO_STATS_2025.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = (row.get("player_id") or "").strip()
            if not pid:
                continue
            stats[pid] = row

    print(f"Loaded 2025 Yahoo stats for {len(stats)} players")
    return stats


def is_pitcher(position: str) -> bool:
    parts = [p.strip().upper() for p in (position or "").split(",")]
    return any(p in {"P", "SP", "RP"} for p in parts)


def parse_ab(h_ab: str) -> int:
    if not h_ab or "/" not in h_ab:
        return 0
    try:
        _h_s, ab_s = h_ab.split("/", 1)
        return int(ab_s)
    except ValueError:
        return 0


def parse_int(val: Any) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return 0


def parse_float(val: Any) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def classify_graduation(player: dict, stats_row: dict) -> tuple[bool, str, dict[str, float]]:
    """Return (eligible, reason, metrics) based on 2025 Yahoo stats + age."""

    age_val = player.get("age")
    age = float(age_val) if isinstance(age_val, (int, float)) else 0.0

    h_ab = (stats_row.get("H/AB") or "").strip()
    ab = parse_ab(h_ab)
    app = parse_int(stats_row.get("APP"))
    ip = parse_float(stats_row.get("IP"))

    pos = player.get("position") or ""
    pitcher = is_pitcher(pos)

    metrics = {
        "age": age,
        "ab": float(ab),
        "app": float(app),
        "ip": float(ip),
    }

    # Age-based auto-grad
    if age and age >= AGE_LIMIT + 1:
        return True, f"age>=26 ({age:.1f})", metrics

    if pitcher:
        if ip >= PITCHER_IP_LIMIT:
            return True, f"IP>={PITCHER_IP_LIMIT} ({ip:.1f})", metrics
        if app >= PITCHER_APP_LIMIT:
            return True, f"APP>={PITCHER_APP_LIMIT} ({app})", metrics
    else:
        if ab >= BATTER_AB_LIMIT:
            return True, f"AB>={BATTER_AB_LIMIT} ({ab})", metrics
        if app >= BATTER_APP_LIMIT:
            return True, f"APP>={BATTER_APP_LIMIT} ({app})", metrics

    return False, "", metrics


def main() -> None:
    players = load_combined_players()
    upid_to_yahoo = load_upid_to_yahoo_id()
    stats_2025 = load_yahoo_stats_2025()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []

    for p in players:
        player_type = (p.get("player_type") or "").strip()
        if player_type != "Farm":
            continue

        # Only consider players who have debuted
        if not p.get("debuted"):
            continue

        upid = str(p.get("upid") or "").strip()
        if not upid:
            continue

        yid = upid_to_yahoo.get(upid)
        if not yid:
            continue

        stats_row = stats_2025.get(yid)
        if not stats_row:
            continue

        eligible, reason, metrics = classify_graduation(p, stats_row)
        if not eligible:
            continue

        row = {
            "upid": upid,
            "name": p.get("name") or "",
            "manager": p.get("manager") or "",
            "team": p.get("team") or "",
            "position": p.get("position") or "",
            "age": metrics["age"],
            "yahoo_player_id": yid,
            "ab_2025": metrics["ab"],
            "app_2025": metrics["app"],
            "ip_2025": metrics["ip"],
            "reason": reason,
        }
        rows.append(row)

    if not rows:
        print("No graduation-eligible Farm players found from 2025 Yahoo stats; not writing CSV")
        return

    fieldnames = [
        "upid",
        "name",
        "manager",
        "team",
        "position",
        "age",
        "yahoo_player_id",
        "ab_2025",
        "app_2025",
        "ip_2025",
        "reason",
    ]

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote graduation-eligible 2025 Yahoo report for {len(rows)} players → {OUT_CSV}")


if __name__ == "__main__":
    main()
