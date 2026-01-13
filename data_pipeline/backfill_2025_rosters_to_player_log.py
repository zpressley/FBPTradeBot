#!/usr/bin/env python3
"""Backfill 2025 roster data into combined_players.json and player_log.json.

Rules (2025 MLB only; do not touch Farm players):
- For every MLB player who ends 2025 on an FBP team, add a Player Log entry:
    update_type: "Roster"
    event: "25 Rosters"
- Ensure all non-rostered MLB players in combined_players.json are marked as:
    manager = null
    contract_type = null
    years_simple = "TC 1"
- For every MLB player removed from a roster in
  data/historical/2025/combined_players_roster_changes_2025.csv, add a
  Player Log entry:
    update_type: "Drop"
    event: "25 Rosters"

We intentionally only log MLB players (player_type == "MLB").
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from player_log import append_entry
DATA_DIR = ROOT / "data"
HIST_2025_DIR = DATA_DIR / "historical" / "2025"

COMBINED_PATH = DATA_DIR / "combined_players.json"
OWNED_2025_CSV = HIST_2025_DIR / "combined_owned_players_2025.csv"
ROSTER_CHANGES_2025_CSV = HIST_2025_DIR / "combined_players_roster_changes_2025.csv"

SEASON = 2025
SOURCE = "2025_rosters"
EVENT_LABEL = "25 Rosters"


def load_combined_players() -> Tuple[List[dict], Dict[str, dict], Dict[str, dict]]:
    with COMBINED_PATH.open("r", encoding="utf-8") as f:
        players: List[dict] = json.load(f)

    by_upid: Dict[str, dict] = {}
    by_yahoo: Dict[str, dict] = {}

    for p in players:
        upid = str(p.get("upid") or "").strip()
        if upid:
            by_upid[upid] = p
        yahoo_id = str(p.get("yahoo_id") or "").strip()
        if yahoo_id:
            by_yahoo[yahoo_id] = p

    return players, by_upid, by_yahoo


def backfill_roster_entries(
    players_by_upid: Dict[str, dict],
    players_by_yahoo: Dict[str, dict],
) -> set:
    """Append Player Log entries for all rostered MLB players.

    Returns a set of UPIDs that are rostered (for later non-rostered cleanup).
    """

    rostered_upids: set = set()

    if not OWNED_2025_CSV.exists():
        print(f"⚠️ Roster CSV not found: {OWNED_2025_CSV}")
        return rostered_upids

    with OWNED_2025_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("player_type") or "").strip() != "MLB":
                continue

            upid_raw = (row.get("upid") or "").strip()
            yahoo_raw = (row.get("yahoo_id") or "").strip()

            rec: Optional[dict] = None
            if upid_raw and upid_raw in players_by_upid:
                rec = players_by_upid[upid_raw]
            elif yahoo_raw and yahoo_raw in players_by_yahoo:
                rec = players_by_yahoo[yahoo_raw]

            if rec is None:
                print(
                    f"⚠️ No combined_players record for rostered MLB "
                    f"{row.get('name')} (UPID={upid_raw}, yahoo_id={yahoo_raw})"
                )
                continue

            if (rec.get("player_type") or "").strip() != "MLB":
                # Safety: do not log Farm players here.
                continue

            upid = str(rec.get("upid") or upid_raw or "").strip()
            if upid:
                rostered_upids.add(upid)

            # Prefer combined_players values where present to keep schemas aligned.
            player_name = rec.get("name") or row.get("name") or ""
            mlb_team = row.get("team") or rec.get("team") or ""
            pos = row.get("position") or rec.get("position") or ""

            contract = (
                row.get("contract_type")
                or rec.get("contract_type")
                or ""
            )
            status = row.get("status") or rec.get("status") or ""
            years = row.get("years_simple") or rec.get("years_simple") or ""

            append_entry(
                season=SEASON,
                source=SOURCE,
                upid=upid,
                player_name=player_name,
                team=mlb_team,
                pos=pos,
                age=rec.get("age"),
                level=str(rec.get("level") or ""),
                team_rank=rec.get("team_rank"),
                rank=rec.get("rank"),
                eta=str(rec.get("eta") or ""),
                player_type="MLB",
                owner=row.get("manager") or "",  # FBP franchise name
                contract=contract,
                status=status,
                years=years,
                update_type="Roster",
                event=EVENT_LABEL,
                admin="2025_roster_backfill",
            )

    print(f"🧾 Wrote roster log entries for {len(rostered_upids)} MLB players")
    return rostered_upids


def update_non_rostered_mlb(players: List[dict], rostered_upids: set) -> int:
    """Ensure non-rostered MLB players are reset in combined_players.json."""

    updated = 0
    for p in players:
        if (p.get("player_type") or "").strip() != "MLB":
            continue

        upid = str(p.get("upid") or "").strip()
        if upid and upid in rostered_upids:
            continue  # still on a team

        changed = False

        if p.get("manager", None) is not None:
            # Represent free agents with explicit null manager.
            p["manager"] = None
            changed = True

        if p.get("contract_type", None) is not None:
            p["contract_type"] = None
            changed = True

        if p.get("years_simple") != "TC 1":
            p["years_simple"] = "TC 1"
            changed = True

        if changed:
            updated += 1

    print(f"🧹 Updated {updated} non-rostered MLB players in combined_players.json")
    return updated


def backfill_drops(
    players: List[dict],
    players_by_yahoo: Dict[str, dict],
) -> int:
    """Append Player Log entries for MLB drops using roster_changes CSV."""

    if not ROSTER_CHANGES_2025_CSV.exists():
        print(f"⚠️ Roster changes CSV not found: {ROSTER_CHANGES_2025_CSV}")
        return 0

    # Helper index by name for when yahoo_id is missing.
    by_name: Dict[str, List[dict]] = {}
    for p in players:
        if (p.get("player_type") or "").strip() != "MLB":
            continue
        name = str(p.get("name") or "").strip()
        if not name:
            continue
        by_name.setdefault(name, []).append(p)

    drop_count = 0

    with ROSTER_CHANGES_2025_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("change_type") or "").strip() != "removed":
                continue
            if (row.get("player_type") or "").strip() != "MLB":
                continue

            yahoo_raw = (row.get("yahoo_id") or "").strip()
            rec: Optional[dict] = None
            if yahoo_raw and yahoo_raw in players_by_yahoo:
                rec = players_by_yahoo[yahoo_raw]
            else:
                name = (row.get("name") or "").strip()
                matches = by_name.get(name) or []
                rec = matches[0] if matches else None

            upid = str(rec.get("upid") or "").strip() if rec else ""
            player_name = (row.get("name") or (rec.get("name") if rec else "")) or ""
            mlb_team = row.get("team") or (rec.get("team") if rec else "") or ""
            pos = row.get("position") or (rec.get("position") if rec else "") or ""

            contract = (rec.get("contract_type") if rec else None) or ""
            status = (rec.get("status") if rec else None) or ""
            years = (rec.get("years_simple") if rec else None) or ""

            append_entry(
                season=SEASON,
                source=SOURCE,
                upid=upid,
                player_name=player_name,
                team=mlb_team,
                pos=pos,
                age=rec.get("age") if rec else None,
                level=str(rec.get("level") or "") if rec else "",
                team_rank=rec.get("team_rank") if rec else None,
                rank=rec.get("rank") if rec else None,
                eta=str(rec.get("eta") or "") if rec else "",
                player_type="MLB",
                owner=row.get("old_manager") or "",  # prior FBP franchise
                contract=contract,
                status=status,
                years=years,
                update_type="Drop",
                event=EVENT_LABEL,
                admin="2025_roster_backfill",
            )
            drop_count += 1

    print(f"🗑️ Wrote drop log entries for {drop_count} MLB players")
    return drop_count


def main() -> None:
    players, by_upid, by_yahoo = load_combined_players()
    print(f"📄 Loaded {len(players)} players from {COMBINED_PATH}")

    rostered_upids = backfill_roster_entries(by_upid, by_yahoo)
    updated_non_rostered = update_non_rostered_mlb(players, rostered_upids)
    drops_logged = backfill_drops(players, by_yahoo)

    # Backup and save combined_players.json if we changed any non-rostered MLB.
    if updated_non_rostered:
        backup_path = COMBINED_PATH.with_name("combined_players_backup_2025_rosters.json")
        with backup_path.open("w", encoding="utf-8") as bf:
            json.dump(players, bf, indent=2)
        print(f"📦 Backup written to {backup_path}")

        with COMBINED_PATH.open("w", encoding="utf-8") as f:
            json.dump(players, f, indent=2)
        print(f"💾 Saved updated combined players to {COMBINED_PATH}")

    print("\n✅ Backfill complete:")
    print(f"   Roster entries: {len(rostered_upids)}")
    print(f"   Drops logged:   {drops_logged}")
    print(f"   MLB FA updated: {updated_non_rostered}")


if __name__ == "__main__":
    main()
