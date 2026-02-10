#!/usr/bin/env python3
"""Set player_type="Farm" for any combined_players rows where it is missing/blank.

This is a one-time cleanup script to ensure every player in combined_players
has a non-empty player_type. We'll treat missing/blank values as Farm for now
and let later pipelines/updaters graduate MLB players or other types.

Behavior:
- Reads data/combined_players.json.
- For each record where player_type is missing, null, or all-whitespace,
  sets player_type = "Farm".
- Writes a one-time backup of the original combined_players.json to
  data/combined_players.player_type_backup.json (if it does not already exist).
- Overwrites data/combined_players.json with the updated records.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMBINED_PATH = ROOT / "data" / "combined_players.json"


def main() -> None:
    if not COMBINED_PATH.exists():
        raise SystemExit(f"ERROR: {COMBINED_PATH} not found")

    raw = COMBINED_PATH.read_text(encoding="utf-8")
    players = json.loads(raw)

    updated = 0
    total = 0

    for p in players:
        total += 1
        pt = (p.get("player_type") or "").strip()
        if not pt:
            p["player_type"] = "Farm"
            updated += 1

    print(f"Scanned {total} players; setting player_type='Farm' for {updated} records with missing/blank player_type")

    if updated == 0:
        print("No changes required; leaving combined_players.json untouched")
        return

    backup_path = COMBINED_PATH.with_suffix(".player_type_backup.json")
    if not backup_path.exists():
        backup_path.write_text(raw, encoding="utf-8")
        print(f"Wrote backup of original combined_players.json to {backup_path}")
    else:
        print(f"Backup {backup_path} already exists; not overwriting it")

    COMBINED_PATH.write_text(
        json.dumps(players, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("Updated combined_players.json with default player_type='Farm' where needed")


if __name__ == "__main__":
    main()
