#!/usr/bin/env python3
"""Promote unowned graduation-eligible 2025 prospects from Farm to MLB.

This script uses the Yahoo-based graduation report:
    data/historical/2026/graduation_eligible_2025_from_yahoo.csv

Behavior:
- Reads data/combined_players.json.
- Reads the graduation-eligible CSV and identifies UPIDs where manager is
  blank/empty (unowned in the current snapshot).
- For those players in combined_players.json:
    * If manager is still blank/empty (safety check), update:
        - player_type   -> "MLB"
        - contract_type -> None
        - status        -> "[5] TC1" (if previously prospect/blank)
        - years_simple  -> "TC 1" (if previously "P" or blank)
- Writes a timestamped backup of combined_players.json before mutating:
    data/combined_players_backup_unowned_graduations_2025_yahoo.json
- Overwrites data/combined_players.json in place.

Owned players in the CSV are left untouched (PAD picks etc. already applied).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, Set

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

COMBINED_PATH = DATA_DIR / "combined_players.json"
GRAD_CSV_PATH = DATA_DIR / "historical" / "2026" / "graduation_eligible_2025_from_yahoo.csv"


def load_unowned_graduation_upids() -> Set[str]:
    """Return set of UPIDs from graduation CSV where manager column is blank."""

    upids: Set[str] = set()
    if not GRAD_CSV_PATH.exists():
        raise SystemExit(f"ERROR: graduation CSV not found at {GRAD_CSV_PATH}")

    with GRAD_CSV_PATH.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            upid = (row.get("upid") or "").strip()
            manager = (row.get("manager") or "").strip()
            if not upid:
                continue
            if manager:
                # Owned: we explicitly skip these per PAD rules
                continue
            upids.add(upid)

    print(f"Loaded {len(upids)} unowned graduation-eligible UPIDs from {GRAD_CSV_PATH}")
    return upids


def main() -> None:
    if not COMBINED_PATH.exists():
        raise SystemExit(f"ERROR: {COMBINED_PATH} not found")

    unowned_upids = load_unowned_graduation_upids()
    if not unowned_upids:
        print("No unowned graduation-eligible players found; nothing to do")
        return

    raw = COMBINED_PATH.read_text(encoding="utf-8")
    players = json.loads(raw)

    updated = 0
    total = len(players)

    for p in players:
        upid = str(p.get("upid") or "").strip()
        if not upid or upid not in unowned_upids:
            continue

        # Double-check still unowned in combined_players snapshot
        manager = (p.get("manager") or "").strip()
        if manager:
            # Ownership changed since CSV was generated; skip safely
            continue

        before_type = p.get("player_type")
        before_status = p.get("status")
        before_years = p.get("years_simple")
        before_contract = p.get("contract_type")

        # Promote to MLB
        p["player_type"] = "MLB"

        # For unowned grads, no contract yet
        p["contract_type"] = None

        # Normalize years_simple if it still looks prospect-y or empty
        years = (before_years or "").strip().upper()
        if not years or years == "P":
            p["years_simple"] = "TC 1"

        # Normalize status if it was blank or clearly prospect-y
        status = (before_status or "").strip()
        if not status or status.endswith("P") or status.endswith("P]"):
            # e.g. "[7] P" -> TC1
            p["status"] = "[5] TC1"

        updated += 1

    print(
        f"Scanned {total} players; promoted {updated} unowned graduation-eligible "
        "prospects from Farm to MLB",
    )

    if updated == 0:
        print("No changes made; leaving combined_players.json untouched")
        return

    backup_path = DATA_DIR / "combined_players_backup_unowned_graduations_2025_yahoo.json"
    if not backup_path.exists():
        backup_path.write_text(raw, encoding="utf-8")
        print(f"Wrote backup of original combined_players.json to {backup_path}")
    else:
        print(f"Backup {backup_path} already exists; not overwriting it")

    COMBINED_PATH.write_text(
        json.dumps(players, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("Updated combined_players.json with MLB promotions for unowned grads")


if __name__ == "__main__":
    main()
