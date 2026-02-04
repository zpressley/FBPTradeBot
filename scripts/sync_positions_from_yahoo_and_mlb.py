#!/usr/bin/env python3
"""Sync player positions in combined_players.json from Yahoo + MLB.

Rules (baked in as a convention going forward):
- For any player that exists in the 2026 Yahoo all-players JSON, use
  Yahoo's `position` field as the canonical position for our data.
- Matching should always prefer:
  1) exact yahoo_id match when available
  2) UPID → yahoo_player_id via data/historical/2026/yahoo_all_players_2026_with_upid.csv
- For players not found in the Yahoo 2026 data, fall back to MLB Stats API
  (via ProspectStatsRepository.fetch_player_stats) using mlb_id from:
  - combined_players.json (mlb_id field), or
  - data/mlb_id_cache.json keyed by UPID.
- Update both `position` and `mlb_primary_position` in combined_players.json
  when we get a better value.

Inputs:
- data/combined_players.json              (source of truth)
- data/yahoo_all_players_2026.json       (Yahoo positions)
- data/historical/2026/yahoo_all_players_2026_with_upid.csv (UPID↔Yahoo)
- data/mlb_id_cache.json                 (UPID→mlb_id)

Output:
- data/combined_players.json (updated, with backup)

Note: this script only adjusts positions; it does not touch contracts,
owners, or any other fields.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Dict, Any

ROOT = Path("/Users/zpressley/fbp-trade-bot")
# Ensure project root is on sys.path so we can import prospect_stats_repository
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prospect_stats_repository import ProspectStatsRepository

DATA_DIR = ROOT / "data"

COMBINED_PATH = DATA_DIR / "combined_players.json"
YAHOO_ALL_2026_JSON = DATA_DIR / "yahoo_all_players_2026.json"
YAHOO_WITH_UPID_2026_CSV = DATA_DIR / "historical" / "2026" / "yahoo_all_players_2026_with_upid.csv"
MLB_ID_CACHE_PATH = DATA_DIR / "mlb_id_cache.json"


def load_json_array(path: Path) -> list:
    if not path.exists():
        raise SystemExit(f"ERROR: {path} not found")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit(f"ERROR: {path} did not contain a JSON array")
    return data


def load_yahoo_positions() -> Dict[str, str]:
    """Return mapping yahoo_id -> Yahoo position string."""
    records = load_json_array(YAHOO_ALL_2026_JSON)
    pos_by_id: Dict[str, str] = {}
    for rec in records:
        pid = str(rec.get("player_id") or "").strip()
        if not pid:
            continue
        pos = (rec.get("position") or "").strip()
        if pos:
            pos_by_id[pid] = pos
    print(f"Loaded Yahoo positions for {len(pos_by_id)} player_ids")
    return pos_by_id


def load_upid_to_yahoo_id() -> Dict[str, str]:
    """Return mapping upid -> yahoo_player_id from with_upid CSV."""
    mapping: Dict[str, str] = {}
    if not YAHOO_WITH_UPID_2026_CSV.exists():
        print(f"WARN: {YAHOO_WITH_UPID_2026_CSV} not found; UPID-based Yahoo mapping will be partial")
        return mapping

    with YAHOO_WITH_UPID_2026_CSV.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            upid = (row.get("upid") or "").strip()
            yid = (row.get("yahoo_player_id") or "").strip()
            if not upid or not yid:
                continue
            mapping[upid] = yid

    print(f"Loaded UPID→Yahoo mapping for {len(mapping)} players from with_upid CSV")
    return mapping


def load_mlb_id_cache() -> Dict[str, Any]:
    if not MLB_ID_CACHE_PATH.exists():
        print(f"WARN: {MLB_ID_CACHE_PATH} not found; MLB-position backfill will be limited")
        return {}
    return json.loads(MLB_ID_CACHE_PATH.read_text(encoding="utf-8"))


def sync_positions() -> None:
    if not COMBINED_PATH.exists():
        raise SystemExit(f"ERROR: {COMBINED_PATH} not found")

    players = json.loads(COMBINED_PATH.read_text(encoding="utf-8"))

    yahoo_pos_by_id = load_yahoo_positions()
    upid_to_yahoo = load_upid_to_yahoo_id()
    mlb_id_cache = load_mlb_id_cache()

    repo = ProspectStatsRepository()

    updated_from_yahoo = 0
    updated_from_mlb = 0

    for p in players:
        # Normalize some fields
        upid = (str(p.get("upid")) if p.get("upid") is not None else "").strip()
        yahoo_id = (str(p.get("yahoo_id")) if p.get("yahoo_id") is not None else "").strip()
        player_type = (p.get("player_type") or "").strip()

        current_pos = (p.get("position") or "").strip()
        current_primary = (p.get("mlb_primary_position") or "").strip()

        # 1) Prefer Yahoo position when available
        target_yid = None
        if yahoo_id and yahoo_id in yahoo_pos_by_id:
            target_yid = yahoo_id
        elif upid and upid in upid_to_yahoo and upid_to_yahoo[upid] in yahoo_pos_by_id:
            target_yid = upid_to_yahoo[upid]

        if target_yid:
            new_pos = yahoo_pos_by_id[target_yid]
            if new_pos and new_pos != current_pos:
                p["position"] = new_pos
                # Seed primary as first token (e.g., "1B,3B" → "1B")
                primary = new_pos.split(",")[0].strip()
                if primary:
                    p["mlb_primary_position"] = primary
                updated_from_yahoo += 1
            elif not current_primary and new_pos:
                primary = new_pos.split(",")[0].strip()
                if primary:
                    p["mlb_primary_position"] = primary
            continue  # Yahoo match wins; no need to hit MLB API

        # 2) For MLB players without Yahoo mapping, try MLB API via mlb_id
        if player_type != "MLB":
            continue

        mlb_id = p.get("mlb_id")
        if not isinstance(mlb_id, int) and upid and upid in mlb_id_cache:
            mlb_id = mlb_id_cache[upid].get("mlb_id")

        if not isinstance(mlb_id, int):
            continue

        stats = repo.fetch_player_stats(mlb_id, p.get("name") or "")
        if not stats:
            continue

        mlb_pos = (stats.get("position") or "").strip()
        if not mlb_pos:
            continue

        if mlb_pos != current_pos:
            p["position"] = mlb_pos
            updated_from_mlb += 1

        if not current_primary or current_primary != mlb_pos:
            p["mlb_primary_position"] = mlb_pos

    # Backup original file once
    backup_path = COMBINED_PATH.with_suffix(".positions_2026_backup.json")
    if not backup_path.exists():
        backup_path.write_text(COMBINED_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Wrote backup to {backup_path}")

    COMBINED_PATH.write_text(json.dumps(players, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Updated positions from Yahoo for {updated_from_yahoo} players")
    print(f"Updated positions from MLB API for {updated_from_mlb} players")


if __name__ == "__main__":
    sync_positions()
