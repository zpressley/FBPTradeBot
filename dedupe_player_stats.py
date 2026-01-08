#!/usr/bin/env python3
"""Deduplicate data/player_stats.json on (UPID, season) or (player_name, season).

Intended for cleanup after accidentally running import scripts multiple times.

Rules:
- Prefer UPID when present: uniqueness is (upid, season).
- If no UPID, fall back to normalized player_name: (player_name.lower().strip(), season).
- Records without a season field are left as-is and never deduplicated.

The script creates a timestamped backup alongside the original file
before writing the deduplicated result.
"""

import json
import os
import shutil
from datetime import datetime
from typing import Any, Dict, List

STATS_FILE = "data/player_stats.json"


def load_stats(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        raise SystemExit(f"❌ Stats file not found: {path}")
    with open(path, "r") as f:
        return json.load(f)


def make_backup(path: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{path}.bak_{ts}"
    shutil.copy2(path, backup_path)
    return backup_path


def dedupe_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return a new list with duplicates removed.

    Duplicate definition:
    - If "upid" is present and truthy: key = ("upid", upid, season)
    - Else if "player_name" is present: key = ("name", normalized_name, season)
    - Else: record is treated as unique (no dedupe key)

    The first occurrence of a key is kept; later ones are dropped.
    """

    seen = set()
    result: List[Dict[str, Any]] = []

    for rec in records:
        season = rec.get("season")
        # Only dedupe records with a concrete season value
        if season is None:
            result.append(rec)
            continue

        upid_raw = rec.get("upid")
        upid = str(upid_raw).strip() if upid_raw not in (None, "") else ""

        if upid:
            key = ("upid", upid, season)
        else:
            name_raw = rec.get("player_name") or rec.get("name")
            if not name_raw:
                # No name/UPID: treat as unique
                result.append(rec)
                continue
            name = str(name_raw).strip().lower()
            key = ("name", name, season)

        if key in seen:
            continue  # drop duplicate

        seen.add(key)
        result.append(rec)

    return result


def main() -> None:
    print("🚿 Deduplicating data/player_stats.json...")
    records = load_stats(STATS_FILE)
    original_count = len(records)

    backup_path = make_backup(STATS_FILE)
    print(f"📦 Backup created: {backup_path}")

    deduped = dedupe_records(records)
    deduped_count = len(deduped)
    removed = original_count - deduped_count

    with open(STATS_FILE, "w") as f:
        json.dump(deduped, f, indent=2)

    size_kb = os.path.getsize(STATS_FILE) / 1024

    print("✅ Deduplication complete")
    print(f"   Original records : {original_count}")
    print(f"   Deduped records  : {deduped_count}")
    print(f"   Removed duplicates: {removed}")
    print(f"   New file size    : {size_kb:.1f} KB")
    print("💡 If something looks wrong, restore from backup:")
    print(f"   mv {backup_path} {STATS_FILE}")


if __name__ == "__main__":
    main()
