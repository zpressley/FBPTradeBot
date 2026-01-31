#!/usr/bin/env python3
"""Export a flat CSV of all Yahoo 2026 players, enriched with UPID.

Inputs:
- data/yahoo_all_players_2026.json (from data_pipeline/fetch_yahoo_all_players.py)
- data/upid_database.json
- data/mlb_team_map.json (for team alias canonicalization)

Output:
- data/historical/2026/yahoo_all_players_2026_with_upid.csv

This is intended as a read-only export and does not affect core processes.
"""

import csv
import json
import os
import re
from typing import Any, Dict, List

DATA_DIR = os.path.join("data")
ALL_PLAYERS_PATH = os.path.join(DATA_DIR, "yahoo_all_players_2026.json")
UPID_DB_PATH = os.path.join(DATA_DIR, "upid_database.json")
TEAM_MAP_PATH = os.path.join(DATA_DIR, "mlb_team_map.json")
OUTPUT_DIR = os.path.join(DATA_DIR, "historical", "2026")
OUTPUT_CSV = os.path.join(OUTPUT_DIR, "yahoo_all_players_2026_with_upid.csv")


def load_json(path: str, default: Any = None) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, "r") as f:
        return json.load(f)


def canonical_team(alias_map: Dict[str, str], team: str) -> str:
    key = (team or "").strip().lower()
    if not key:
        return ""
    return alias_map.get(key, team)


def normalize_name(name: str) -> str:
    """Normalize a baseball player name for matching.

    - Lowercase
    - Strip leading/trailing whitespace
    - Remove periods and apostrophes ("A.J." -> "aj", "O'Neil" -> "oneil")
    - Collapse multiple spaces
    """

    if not name:
        return ""
    s = name.strip().lower()
    # Remove common punctuation that varies between sources
    s = s.replace(".", "").replace("'", "")
    # Collapse multiple whitespace to single spaces
    s = re.sub(r"\s+", " ", s)
    return s


def build_name_index(upid_db: Dict[str, Any]) -> Dict[str, List[str]]:
    """Ensure we have a rich name_index structure.

    We want to match on:
    - Exact sheet names
    - Alternate names (including accent-stripped variants)
    - Normalized forms (no dots/apostrophes, collapsed spaces)
    """

    by_upid = upid_db.get("by_upid", {})
    base_index: Dict[str, List[str]] = upid_db.get("name_index") or {}

    rebuilt: Dict[str, List[str]] = {}

    def add_key(idx: Dict[str, List[str]], key: str, upid: str) -> None:
        k = key.strip().lower()
        if not k:
            return
        idx.setdefault(k, []).append(upid)

    # 1) Start from any existing name_index in the file
    for key, upids in base_index.items():
        for upid in upids:
            add_key(rebuilt, key, upid)
            norm = normalize_name(key)
            if norm and norm != key:
                add_key(rebuilt, norm, upid)

    # 2) Ensure all by_upid entries (name + alt_names) are present and normalized
    for upid, rec in by_upid.items():
        primary = (rec.get("name") or "").strip()
        if primary:
            add_key(rebuilt, primary, upid)
            norm = normalize_name(primary)
            if norm and norm != primary.lower():
                add_key(rebuilt, norm, upid)

        for alt in rec.get("alt_names", []) or []:
            alt_str = str(alt).strip()
            if not alt_str:
                continue
            add_key(rebuilt, alt_str, upid)
            norm_alt = normalize_name(alt_str)
            if norm_alt and norm_alt != alt_str.lower():
                add_key(rebuilt, norm_alt, upid)

    return rebuilt


def find_upid_for_player(
    name_index: Dict[str, List[str]],
    by_upid: Dict[str, Any],
    alias_map: Dict[str, str],
    name: str,
    mlb_team: str,
) -> str:
    """Best-effort UPID lookup using UPID DB name index and team.

    Mirrors the logic in data_pipeline/merge_players.py so that Yahoo-only
    players still flow through UPID as the primary identifier.
    """

    raw_key = (name or "").strip().lower()
    norm_key = normalize_name(name)
    if not raw_key and not norm_key:
        return ""

    candidates: List[str] = []
    if raw_key:
        candidates = name_index.get(raw_key, []) or []
    if not candidates and norm_key:
        candidates = name_index.get(norm_key, []) or []
    if not candidates:
        return ""

    # Deduplicate while preserving order so duplicate entries of the same UPID
    # (from primary name + alt_names) don't falsely look like multi-player
    # ambiguity.
    seen: set[str] = set()
    unique_candidates: List[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique_candidates.append(c)

    if len(unique_candidates) == 1 or not mlb_team:
        return unique_candidates[0]

    canon_yahoo = canonical_team(alias_map, mlb_team)
    narrowed: List[str] = []
    for upid in unique_candidates:
        rec = by_upid.get(upid) or {}
        rec_team = rec.get("team") or ""
        if not rec_team:
            narrowed.append(upid)
            continue
        if canonical_team(alias_map, rec_team) == canon_yahoo:
            narrowed.append(upid)

    if len(narrowed) == 1:
        return narrowed[0]
    return ""


def export_csv() -> None:
    players = load_json(ALL_PLAYERS_PATH, default=[])
    if not players:
        raise SystemExit(
            f"No players found at {ALL_PLAYERS_PATH}. "
            "Run data_pipeline/fetch_yahoo_all_players.py for the 2026 season first."
        )

    upid_db = load_json(UPID_DB_PATH, default={"by_upid": {}, "name_index": {}})
    by_upid: Dict[str, Any] = upid_db.get("by_upid", {})
    alias_map = load_json(TEAM_MAP_PATH, default={}).get("aliases", {})
    name_index = build_name_index(upid_db)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    fieldnames = [
        "upid",
        "yahoo_player_id",
        "yahoo_player_key",
        "name",
        "first_name",
        "last_name",
        "mlb_team",
        "mlb_team_full",
        "position",
        "eligible_positions",
        "ownership_type",
        "owned_by_team_key",
        "percent_owned",
    ]

    with open(OUTPUT_CSV, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for p in players:
            name = p.get("name") or ""
            mlb_team = p.get("team") or ""
            upid = find_upid_for_player(name_index, by_upid, alias_map, name, mlb_team)

            row = {
                "upid": upid,
                "yahoo_player_id": p.get("player_id", ""),
                "yahoo_player_key": p.get("player_key", ""),
                "name": name,
                "first_name": p.get("first_name", ""),
                "last_name": p.get("last_name", ""),
                "mlb_team": mlb_team,
                "mlb_team_full": p.get("team_full", ""),
                "position": p.get("position", ""),
                "eligible_positions": "|".join(p.get("eligible_positions", []) or []),
                "ownership_type": p.get("ownership_type", ""),
                "owned_by_team_key": p.get("owned_by", ""),
                "percent_owned": p.get("percent_owned", 0),
            }

            writer.writerow(row)

    print(f"✅ Wrote {OUTPUT_CSV}")


if __name__ == "__main__":
    export_csv()