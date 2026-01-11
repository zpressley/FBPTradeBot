#!/usr/bin/env python3
"""Annotate combined_players.json with MLB service days for Farm players.

Steps:
1) Load data/service_stats.json (output of service_time/service_days_tracker.py).
2) Build a mapping upid -> active_days.
3) Load data/combined_players.json.
4) For each record with player_type == "Farm" and a matching upid in stats,
   set `service_time_days` to the active_days value.
5) Write a timestamped backup of combined_players.json, then save the updated file.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
COMBINED_PATH = DATA_DIR / "combined_players.json"
STATS_PATH = DATA_DIR / "service_stats.json"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def make_backup(path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_suffix(path.suffix + f".bak_{ts}")
    backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup_path


def main() -> None:
    print("🔄 Updating combined_players.json with service_time_days for Farm players...")

    if not STATS_PATH.exists():
        raise SystemExit(f"Missing {STATS_PATH} - run service_time/service_days_tracker.py first.")

    stats = load_json(STATS_PATH)
    combined = load_json(COMBINED_PATH)

    # Build upid -> active_days map from stats
    # If a player has MLB debut/career stats but 0 recorded active_days
    # (e.g., roster events incomplete), treat them as having at least 1 day.
    upid_to_days: Dict[str, int] = {}
    for name, entry in stats.items():
        upid = str(entry.get("upid") or "").strip()
        if not upid:
            continue

        days = entry.get("active_days")
        if days is None:
            days = 0

        # MLB usage in the current season
        has_current_usage = any([
            (entry.get("at_bats") or 0) > 0,
            (entry.get("innings_pitched") or 0) > 0,
            (entry.get("pitching_appearances") or 0) > 0,
        ])

        # Career/debut hints from career_stats (if present)
        career = entry.get("career_stats") or {}
        has_debut_flag = bool(career.get("debut_year"))
        has_career_usage = any(
            bool(career.get(k))
            for k in ("career_games", "career_at_bats", "career_innings", "career_appearances")
        )

        # If we know they have MLB usage (current or historical) but no
        # roster-events days, give them a minimum of 1 day.
        if (not days) and (has_current_usage or has_debut_flag or has_career_usage):
            days = 1

        if days is None:
            continue
        upid_to_days[upid] = int(days)

    print(f"📊 Found service days for {len(upid_to_days)} UPIDs")

    updated_count = 0

    for rec in combined:
        if (rec.get("player_type") or "") != "Farm":
            continue
        upid = str(rec.get("upid") or "").strip()
        if not upid:
            continue
        days = upid_to_days.get(upid)
        if days is None:
            continue

        # Only set/overwrite if value changes to avoid noisy diffs
        if rec.get("service_time_days") != days:
            rec["service_time_days"] = days
            updated_count += 1

    if updated_count == 0:
        print("No Farm players updated with service_time_days (either already set or no matches).")
        return

    backup = make_backup(COMBINED_PATH)
    print(f"📦 Backup of combined_players.json written to {backup}")

    save_json(COMBINED_PATH, combined)
    print(f"✅ Updated service_time_days for {updated_count} Farm players.")


if __name__ == "__main__":
    main()
