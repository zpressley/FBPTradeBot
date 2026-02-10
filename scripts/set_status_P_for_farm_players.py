#!/usr/bin/env python3
"""Set status="P" for prospect (Farm) players in combined_players.json.

Behavior:
- Reads data/combined_players.json.
- For each record where player_type == "Farm" (case-insensitive) and
  status is missing/empty/whitespace, sets status = "P".
- Writes a one-time backup of the original combined_players.json to
  data/combined_players.status_P_backup.json (if it does not already exist).
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

    total = 0
    updated = 0

    for p in players:
        total += 1
        pt = (p.get("player_type") or "").strip().lower()
        status_val = (p.get("status") or "").strip()

        if pt == "farm" and not status_val:
            p["status"] = "P"
            updated += 1

    print(
        f"Scanned {total} players; set status='P' for {updated} Farm players "
        "with missing/blank status",
    )

    if updated == 0:
        print("No changes required; leaving combined_players.json untouched")
        return

    backup_path = COMBINED_PATH.with_suffix(".status_P_backup.json")
    if not backup_path.exists():
        backup_path.write_text(raw, encoding="utf-8")
        print(f"Wrote backup of original combined_players.json to {backup_path}")
    else:
        print(f"Backup {backup_path} already exists; not overwriting it")

    COMBINED_PATH.write_text(
        json.dumps(players, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("Updated combined_players.json with status='P' for Farm prospects where needed")


if __name__ == "__main__":
    main()
