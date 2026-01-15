#!/usr/bin/env python3
"""Update fbp-hub/data/top100_prospects.json with IDs from trade-bot data.

This script is intended to run from the fbp-trade-bot repo and update the
Top 100 prospects JSON in the fbp-hub repo by:

- Filling in missing UPIDs by matching names via upid_database.json.
- Filling in missing mlb_id values from mlb_id_cache.json and/or
  data/combined_players.json.
- Filling in yahoo_id and FBP_Team from combined_players.json where possible.

It is designed to be idempotent and safe to run daily as part of the pipeline.

Usage (local):
  python3 data_pipeline/update_top100_prospects_for_hub.py \
      --hub-path ../fbp-hub

Usage (GitHub Actions):
  - Check out fbp-trade-bot (this repo)
  - Check out fbp-hub into a subdirectory (e.g. `fbp-hub/`)
  - Run:
      python3 data_pipeline/update_top100_prospects_for_hub.py --hub-path fbp-hub
"""

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")


def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def build_indices() -> Tuple[
    Dict[str, Dict[str, Any]],  # combined_by_upid
    Dict[str, List[Dict[str, Any]]],  # combined_by_name
    Dict[str, Dict[str, Any]],  # upid_by_upid
    Dict[str, List[str]],  # upid_name_index
    Dict[str, int],  # mlb_id_by_upid
    Dict[str, List[str]],  # org_to_team_aliases
]:
    """Load trade-bot data and build lookup indices."""

    combined_path = os.path.join(DATA_DIR, "combined_players.json")
    upid_db_path = os.path.join(DATA_DIR, "upid_database.json")
    team_map_path = os.path.join(DATA_DIR, "mlb_team_map.json")
    mlb_cache_path = os.path.join(DATA_DIR, "mlb_id_cache.json")

    combined = load_json(combined_path)
    upid_db = load_json(upid_db_path)
    team_map = load_json(team_map_path) if os.path.exists(team_map_path) else {}
    mlb_cache_raw = load_json(mlb_cache_path) if os.path.exists(mlb_cache_path) else {}

    combined_by_upid: Dict[str, Dict[str, Any]] = {}
    combined_by_name: Dict[str, List[Dict[str, Any]]] = {}

    for p in combined:
        upid = str(p.get("upid", "")).strip()
        if upid:
            combined_by_upid[upid] = p
        name = p.get("name")
        if name:
            key = name.strip().lower()
            combined_by_name.setdefault(key, []).append(p)

    upid_by_upid: Dict[str, Dict[str, Any]] = upid_db.get("by_upid", {})
    upid_name_index: Dict[str, List[str]] = upid_db.get("name_index", {})

    mlb_id_by_upid: Dict[str, int] = {}
    for key, rec in mlb_cache_raw.items():
        if not isinstance(rec, dict):
            continue
        mid = rec.get("mlb_id")
        if isinstance(mid, int):
            mlb_id_by_upid[str(key)] = mid

    # Build org -> team alias list from mlb_team_map.json
    org_to_team_aliases: Dict[str, List[str]] = {}
    official = team_map.get("official", {})
    for official_name, rec in official.items():
        aliases = rec.get("aliases", []) or []
        codes = [a.strip() for a in aliases if a.strip()]
        org_to_team_aliases[official_name.strip().lower()] = codes

    return (
        combined_by_upid,
        combined_by_name,
        upid_by_upid,
        upid_name_index,
        mlb_id_by_upid,
        org_to_team_aliases,
    )


def pick_upid_for_entry(
    name: str,
    org: str,
    upid_name_index: Dict[str, List[str]],
    upid_by_upid: Dict[str, Dict[str, Any]],
    org_to_team_aliases: Dict[str, List[str]],
) -> Optional[str]:
    """Given a Top 100 entry name + org, choose the best UPID.

    Priority:
    1) Name-indexed UPIDs (from upid_database) filtered by matching team.
    2) If only one name-indexed UPID, use it.
    3) Otherwise, return None and let caller fall back to combined_players
       name matching if desired.
    """

    key = name.strip().lower()
    candidates = upid_name_index.get(key) or []
    if not candidates:
        return None

    org_key = org.strip().lower()
    org_aliases = set(org_to_team_aliases.get(org_key, []))

    # Filter by matching team when possible
    if org_aliases:
        filtered: List[str] = []
        for upid in candidates:
            rec = upid_by_upid.get(upid)
            if not rec:
                continue
            team = (rec.get("team") or "").strip()
            if team and team in org_aliases:
                filtered.append(upid)
        if len(filtered) == 1:
            return filtered[0]
        if len(filtered) > 1:
            # Ambiguous, but better than nothing: pick first deterministically
            return sorted(filtered)[0]

    # Fallback: single candidate by name
    if len(candidates) == 1:
        return candidates[0]

    # Ambiguous; do not guess here
    return None


def enrich_entry(
    entry: Dict[str, Any],
    combined_by_upid: Dict[str, Dict[str, Any]],
    combined_by_name: Dict[str, List[Dict[str, Any]]],
    upid_by_upid: Dict[str, Dict[str, Any]],
    upid_name_index: Dict[str, List[str]],
    mlb_id_by_upid: Dict[str, int],
    org_to_team_aliases: Dict[str, List[str]],
) -> None:
    """Mutate a single Top 100 entry in-place with best-available IDs."""

    name = entry.get("name", "").strip()
    org = entry.get("org", "").strip()

    existing_upid = entry.get("upid")
    upid: Optional[str]
    if existing_upid:
        upid = str(existing_upid)
    else:
        # Try UPID database by name (with team context)
        upid = pick_upid_for_entry(
            name=name,
            org=org,
            upid_name_index=upid_name_index,
            upid_by_upid=upid_by_upid,
            org_to_team_aliases=org_to_team_aliases,
        )

        # Fallback: try combined_players name-only match
        if upid is None:
            key = name.lower()
            candidates = combined_by_name.get(key) or []
            if len(candidates) == 1:
                cand_upid = str(candidates[0].get("upid", "")).strip()
                if cand_upid:
                    upid = cand_upid

    if not upid:
        # Nothing to do
        return

    # Normalize UPID field on entry
    entry["upid"] = upid

    combined_rec = combined_by_upid.get(upid, {})

    # MLB ID priority: combined_players.mlb_id, then mlb_id_cache.json
    mlb_id: Optional[int] = None
    if isinstance(combined_rec.get("mlb_id"), int):
        mlb_id = combined_rec["mlb_id"]
    elif upid in mlb_id_by_upid:
        mlb_id = mlb_id_by_upid[upid]

    entry["mlb_id"] = mlb_id

    # Yahoo ID and FBP_Team from combined if not already set
    if (entry.get("yahoo_id") in (None, "")) and combined_rec.get("yahoo_id"):
        entry["yahoo_id"] = combined_rec["yahoo_id"]

    if (entry.get("FBP_Team") in (None, "")) and combined_rec.get("FBP_Team") is not None:
        entry["FBP_Team"] = combined_rec["FBP_Team"]


def update_top100(hub_path: str) -> None:
    top_path = os.path.join(hub_path, "data", "top100_prospects.json")
    if not os.path.exists(top_path):
        raise SystemExit(f"top100_prospects.json not found at {top_path}")

    print(f"📄 Loading Top 100 prospects from {top_path}...")
    entries = load_json(top_path)
    if not isinstance(entries, list):
        raise SystemExit("top100_prospects.json is not a list; unexpected format")

    (
        combined_by_upid,
        combined_by_name,
        upid_by_upid,
        upid_name_index,
        mlb_id_by_upid,
        org_to_team_aliases,
    ) = build_indices()

    updated_count = 0
    created_ids = 0

    for e in entries:
        before_upid = e.get("upid")
        before_mlb = e.get("mlb_id")
        before_yahoo = e.get("yahoo_id")
        before_team = e.get("FBP_Team")

        enrich_entry(
            e,
            combined_by_upid,
            combined_by_name,
            upid_by_upid,
            upid_name_index,
            mlb_id_by_upid,
            org_to_team_aliases,
        )

        if (
            e.get("upid") != before_upid
            or e.get("mlb_id") != before_mlb
            or e.get("yahoo_id") != before_yahoo
            or e.get("FBP_Team") != before_team
        ):
            updated_count += 1
            if before_upid in (None, "") and e.get("upid") not in (None, ""):
                created_ids += 1

    # Keep entries sorted by rank, just in case
    entries.sort(key=lambda x: x.get("rank", 0))

    save_json(top_path, entries)

    print(
        f"✅ Updated Top 100 prospects at {top_path}: "
        f"{updated_count} entries changed, {created_ids} gained new UPIDs/IDs."
    )


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Update fbp-hub Top 100 prospects JSON with IDs from trade-bot data.")
    parser.add_argument(
        "--hub-path",
        default=os.path.join(ROOT_DIR, "..", "fbp-hub"),
        help="Path to the fbp-hub repository root (default: ../fbp-hub)",
    )
    args = parser.parse_args(argv)

    hub_path = os.path.abspath(args.hub_path)
    if not os.path.isdir(hub_path):
        raise SystemExit(f"Hub path {hub_path} is not a directory; set --hub-path appropriately")

    update_top100(hub_path)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted by user", file=sys.stderr)
        sys.exit(1)
