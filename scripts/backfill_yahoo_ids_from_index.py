#!/usr/bin/env python3
"""Backfill missing yahoo_id in combined_players.json using Yahoo index + UPID.

Rules / behavior:
- Only fills yahoo_id when it is currently missing/blank.
- Uses UPID as the spine: requires that the combined_players row has a UPID
  present in data/upid_database.json (by_upid).
- Matching keys are (normalized_name, canonical_team), where canonical_team
  is derived from data/mlb_team_map.json.
- Uses data/yahoo_player_index.json as the source of Yahoo players.
- Only writes yahoo_id when there is exactly ONE matching Yahoo player.
- Writes a single JSON backup of combined_players.json before mutating.

This script is idempotent and safe to re-run.

Usage (from repo root):

    python scripts/backfill_yahoo_ids_from_index.py

"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

COMBINED_PATH = DATA_DIR / "combined_players.json"
UPID_DB_PATH = DATA_DIR / "upid_database.json"
YAHOO_INDEX_PATH = DATA_DIR / "yahoo_player_index.json"
TEAM_MAP_PATH = DATA_DIR / "mlb_team_map.json"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_team_alias_map(team_map: dict) -> Dict[str, str]:
    """Return alias -> official team code mapping from mlb_team_map.json."""

    alias_to_official: Dict[str, str] = {}
    for official, info in (team_map.get("official") or {}).items():
        official_u = official.upper()
        alias_to_official[official_u] = official_u
        for a in info.get("aliases", []):
            alias_to_official[str(a).upper()] = official_u
    return alias_to_official


def canon_team(raw: Optional[str], alias_map: Dict[str, str]) -> str:
    if not raw:
        return ""
    c = str(raw).strip().upper()
    return alias_map.get(c, c)


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def norm_name(name: Optional[str]) -> str:
    if not name:
        return ""
    s = name.lower().strip()
    return _NON_ALNUM.sub("", s)


def build_yahoo_name_team_index(yahoo_index: dict, alias_map: Dict[str, str]) -> Dict[Tuple[str, str], Set[str]]:
    """Index Yahoo players by (normalized_name, canonical_team).

    yahoo_index schema:
        { yahoo_id: {"name": str, "team": str, ...}, ... }
    """

    index: Dict[Tuple[str, str], Set[str]] = {}
    for yid, info in yahoo_index.items():
        name = info.get("name") or ""
        team = info.get("team") or ""
        nn = norm_name(name)
        if not nn:
            continue
        ct = canon_team(team, alias_map)
        key = (nn, ct)
        index.setdefault(key, set()).add(str(yid))
    return index


def main() -> None:
    if not COMBINED_PATH.exists():
        raise SystemExit(f"ERROR: {COMBINED_PATH} not found")

    combined = load_json(COMBINED_PATH)
    upid_db = load_json(UPID_DB_PATH)
    yahoo_index = load_json(YAHOO_INDEX_PATH)
    team_map = load_json(TEAM_MAP_PATH)

    by_upid: dict = upid_db.get("by_upid") or {}
    alias_map = build_team_alias_map(team_map)
    yahoo_name_team_index = build_yahoo_name_team_index(yahoo_index, alias_map)

    print(f"Loaded {len(combined)} combined_players rows")
    print(f"Loaded {len(by_upid)} UPID records from upid_database")
    print(f"Loaded {len(yahoo_index)} Yahoo players into index")

    updated = 0
    examined = 0

    for p in combined:
        existing_yid = str(p.get("yahoo_id") or "").strip()
        if existing_yid:
            continue  # do not overwrite existing assignments

        upid = str(p.get("upid") or "").strip()
        if not upid:
            continue

        upid_rec = by_upid.get(upid)
        if not upid_rec:
            continue

        examined += 1

        base_name = upid_rec.get("name") or upid_rec.get("Name") or ""
        alt_names = upid_rec.get("alt_names") or []
        if not isinstance(alt_names, list):
            alt_names = []
        cp_name = p.get("name") or ""

        # Candidate names (deduplicated, best-first)
        name_candidates: List[str] = []
        for n in [base_name, cp_name, *alt_names]:
            if n and n not in name_candidates:
                name_candidates.append(n)

        if not name_candidates:
            continue

        # Canonical team from UPID-team first, falling back to combined_players.team
        team_code = (
            upid_rec.get("team")
            or upid_rec.get("Team")
            or p.get("team")
            or ""
        )
        canonical_team = canon_team(team_code, alias_map)

        candidate_yids: Set[str] = set()
        for raw_name in name_candidates:
            nn = norm_name(raw_name)
            if not nn:
                continue
            key = (nn, canonical_team)
            ids = yahoo_name_team_index.get(key)
            if not ids:
                continue
            candidate_yids.update(ids)

        if len(candidate_yids) == 1:
            yid = next(iter(candidate_yids))
            p["yahoo_id"] = yid
            updated += 1
        elif candidate_yids:
            # Ambiguous; leave blank and optionally log in the future if needed.
            continue

    print(f"Examined {examined} UPID-backed players without yahoo_id")
    print(f"Filled yahoo_id for {updated} players")

    if updated == 0:
        print("No changes to write; exiting without touching combined_players.json")
        return

    # Backup once per run
    backup_path = COMBINED_PATH.with_suffix(".yahoo_id_backup.json")
    if not backup_path.exists():
        backup_path.write_text(COMBINED_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Wrote backup to {backup_path}")

    COMBINED_PATH.write_text(json.dumps(combined, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Updated combined_players.json with {updated} new yahoo_id values")


if __name__ == "__main__":
    main()
