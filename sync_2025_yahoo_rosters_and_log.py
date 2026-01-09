#!/usr/bin/env python3
"""Sync 2025 Yahoo final rosters into combined_players.json and log changes.

Behavior
--------
- Uses data/historical/2025/yahoo_players_2025.json (final 2025 Yahoo rosters)
  to determine the canonical MLB ownership for players with a yahoo_id.
- For every MLB player in data/combined_players.json that has a yahoo_id:
  - Update its FBP_Team + manager based on the final 2025 rosters.
  - If the player was previously owned but is unowned in Yahoo, clear owner.
  - If ownership changes (including to/from unowned), append an entry to
    data/player_log.json using player_log.append_entry with:
      season: 2025
      source: "admin_roster_sync"
      admin: "admin"
      update_type: "admin"
      event: "25 Rosters"

This is the first round of structured entries into player_log.json.
"""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from player_log import append_entry

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
COMBINED_PATH = DATA_DIR / "combined_players.json"
YAHOO_2025_PATH = DATA_DIR / "historical" / "2025" / "yahoo_players_2025.json"
MANAGERS_CONFIG_PATH = ROOT.parent / "fbp-hub" / "config" / "managers.json"


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


def load_manager_teams() -> Dict[str, Dict[str, Any]]:
    cfg = load_json(MANAGERS_CONFIG_PATH)
    teams = cfg.get("teams", {})
    return {abbr.strip().upper(): info for abbr, info in teams.items()}


def build_final_owner_map(final_data: Dict[str, List[Dict[str, Any]]]) -> Dict[str, str]:
    """Return mapping yahoo_id -> final team abbr (e.g., "WIZ")."""

    owner_by_yahoo: Dict[str, str] = {}

    for team_abbr, players in final_data.items():
        for p in players:
            yid_raw = p.get("yahoo_id")
            if yid_raw is None:
                continue
            yid = str(yid_raw).strip()
            if not yid:
                continue

            if yid in owner_by_yahoo and owner_by_yahoo[yid] != team_abbr:
                print(
                    f"⚠️ yahoo_id {yid} appears for multiple teams: "
                    f"{owner_by_yahoo[yid]} and {team_abbr}; keeping first",
                )
                continue

            owner_by_yahoo[yid] = str(team_abbr).strip().upper()

    return owner_by_yahoo


def main() -> None:
    print("🔄 Syncing 2025 Yahoo rosters into combined_players.json and player_log.json ...")

    combined = load_json(COMBINED_PATH)
    yahoo_2025 = load_json(YAHOO_2025_PATH)
    manager_teams = load_manager_teams()
    owner_by_yahoo = build_final_owner_map(yahoo_2025)

    original = copy.deepcopy(combined)

    # Index original by yahoo_id for quick lookup of old ownership
    original_by_yid: Dict[str, Dict[str, Any]] = {}
    for rec in original:
        yid_raw = rec.get("yahoo_id")
        if yid_raw is None:
            continue
        yid = str(yid_raw).strip()
        if not yid:
            continue
        original_by_yid[yid] = rec

    changes = 0

    for rec in combined:
        player_type = rec.get("player_type") or ""
        if player_type != "MLB":
            continue

        yid_raw = rec.get("yahoo_id")
        if yid_raw is None:
            continue
        yid = str(yid_raw).strip()
        if not yid:
            continue

        orig = original_by_yid.get(yid, {})

        old_fbp_team = (orig.get("FBP_Team") or rec.get("FBP_Team") or "").strip()
        old_manager = (orig.get("manager") or rec.get("manager") or "").strip()

        final_abbr = owner_by_yahoo.get(yid)  # may be None -> unowned

        if not final_abbr:
            new_fbp_team = ""
            new_manager = ""
        else:
            team_info = manager_teams.get(final_abbr, {})
            new_fbp_team = final_abbr
            new_manager = (team_info.get("name") or final_abbr).strip()

        if old_fbp_team == new_fbp_team and old_manager == new_manager:
            # No change in ownership; skip
            continue

        # Apply update
        rec["FBP_Team"] = new_fbp_team
        rec["manager"] = new_manager
        changes += 1

        # Log into player_log.json
        append_entry(
            season=2025,
            source="admin_roster_sync",
            admin="admin",
            upid=str(rec.get("upid") or ""),
            player_name=str(rec.get("name") or ""),
            team=str(rec.get("team") or ""),
            pos=str(rec.get("position") or ""),
            level="MLB",  # this log is explicitly for MLB owners
            player_type="MLB",
            owner=new_manager,
            contract=str(rec.get("contract_type") or ""),
            status=str(rec.get("status") or ""),
            years=str(rec.get("years_simple") or ""),
            update_type="admin",
            event="25 Rosters",
        )

    if changes == 0:
        print("No MLB ownership changes detected.")
        return

    backup = make_backup(COMBINED_PATH)
    print(f"📦 Backup of combined_players.json written to {backup}")

    save_json(COMBINED_PATH, combined)
    print(f"✅ Applied {changes} MLB ownership updates based on 2025 Yahoo rosters.")
    print("   Player log entries appended with update_type='admin', event='25 Rosters'.")


if __name__ == "__main__":
    main()
