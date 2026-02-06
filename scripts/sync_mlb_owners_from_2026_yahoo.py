#!/usr/bin/env python3
"""Sync MLB owners in combined_players.json from 2026 Yahoo rosters.

This script treats **2026 Yahoo all-players** as the source of truth for
current MLB ownership and updates the FBP_Team field for MLB players in the
hub's combined_players.json accordingly.

Inputs (read-only):
- data/yahoo_all_players_2026.json
- data/historical/2026/yahoo_all_players_2026_with_upid.csv (for ID mapping)
- data/historical/2025/yahoo_owned_players_2025.csv (for FBP team metadata)

Output (write):
- ../fbp-hub/data/combined_players.json (with backup alongside it)

Behavior:
- Only updates records where player_type == "MLB" in combined_players.json.
- For each MLB player, we try to determine 2026 FBP team:
  - Prefer matching by yahoo_id when present.
  - Otherwise, if upid is present, resolve via the 2026 with_upid CSV.
- If 2026 Yahoo says the player is unowned (free agent or missing owned_by),
  we set FBP_Team = "".
- If 2026 Yahoo maps them to an FBP team, we set FBP_Team to that abbr.

This is intentionally separate from the 2025 end-of-season owner sync so that
we can explicitly move to 2026 rosters when desired.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, Any, Tuple

TRADE_BOT_ROOT = Path("/Users/zpressley/fbp-trade-bot")
HUB_ROOT = Path("/Users/zpressley/fbp-hub")

YAHOO_ALL_2026_JSON = TRADE_BOT_ROOT / "data/yahoo_all_players_2026.json"
YAHOO_WITH_UPID_2026_CSV = TRADE_BOT_ROOT / "data/historical/2026/yahoo_all_players_2026_with_upid.csv"
YAHOO_OWNED_2025_CSV = TRADE_BOT_ROOT / "data/historical/2025/yahoo_owned_players_2025.csv"

# Primary source of truth for combined_players lives in the trade-bot repo;
# the hub copy is a derivative that will be overwritten from this file.
COMBINED_PLAYERS_JSON = TRADE_BOT_ROOT / "data/combined_players.json"
HUB_COMBINED_PLAYERS_JSON = HUB_ROOT / "data/combined_players.json"


def load_json_array(path: Path) -> list:
    if not path.exists():
        raise SystemExit(f"ERROR: {path} not found")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit(f"ERROR: {path} did not contain a JSON array")
    return data


def load_yahoo_id_to_upid() -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    if not YAHOO_WITH_UPID_2026_CSV.exists():
        print(f"WARN: {YAHOO_WITH_UPID_2026_CSV} not found; UPID-based mapping will be partial")
        return mapping

    with YAHOO_WITH_UPID_2026_CSV.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yahoo_id = (row.get("yahoo_player_id") or "").strip()
            upid = (row.get("upid") or "").strip()
            if not yahoo_id or not upid:
                continue
            mapping[yahoo_id] = upid
    print(f"Loaded {len(mapping)} yahoo_id→upid mappings from with_upid CSV")
    return mapping


def load_team_mappings() -> Tuple[Dict[int, Dict[str, str]], Dict[str, Dict[str, str]]]:
    """Return Yahoo 2026 team-slot → FBP metadata.

    We reuse the same mapping used in sync_positions_and_check_ownership.py:
    explicit slot→abbr for league 469, with metadata taken from the 2025
    yahoo_owned_players_2025.csv so we don’t duplicate team names.
    """
    if not YAHOO_OWNED_2025_CSV.exists():
        print(f"WARN: {YAHOO_OWNED_2025_CSV} not found; team metadata will be minimal")
        return {}

    abbr_meta: Dict[str, Dict[str, str]] = {}
    with YAHOO_OWNED_2025_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            abbr = (row.get("fbp_abbr") or "").strip()
            if not abbr or abbr in abbr_meta:
                continue
            abbr_meta[abbr] = {
                "fbp_abbr": abbr,
                "team_name": (row.get("team_name") or "").strip(),
                "manager_name": (row.get("manager_name") or "").strip(),
            }

    # 2026: explicit slot → abbr mapping from prior work
    slot_to_abbr_2026 = {
        1: "WIZ",
        12: "WAR",
        2: "DRO",
        3: "B2J",
        6: "JEP",
        8: "DMN",
        4: "CFL",
        9: "SAD",
        5: "HAM",
        7: "LFB",
        10: "RV",
        11: "TBB",
    }

    mapping_2026: Dict[int, Dict[str, str]] = {}
    for team_id, abbr in slot_to_abbr_2026.items():
        meta = abbr_meta.get(abbr, {"fbp_abbr": abbr, "team_name": "", "manager_name": ""})
        mapping_2026[team_id] = meta

    print(f"Loaded FBP team metadata for {len(mapping_2026)} Yahoo 2026 slots")
    return mapping_2026, abbr_meta


def decode_team(owner_key: str, map_2026: Dict[int, Dict[str, str]]) -> Tuple[int | None, str]:
    """Given a Yahoo owned_by key, return (team_id, fbp_abbr).

    owner_key examples: "469.l.8560.t.3" or "469.l.8560.t.7".
    """
    owner_key = (owner_key or "").strip()
    if not owner_key:
        return None, ""

    try:
        tail = owner_key.split(".t.")[-1]
        team_id = int(tail)
    except Exception:
        return None, ""

    if not owner_key.startswith("469."):
        # Only care about the 2026 league
        return team_id, ""

    meta = map_2026.get(team_id) or {}
    return team_id, meta.get("fbp_abbr", "")


def build_yahoo_2026_owner_map() -> Tuple[Dict[str, str], Dict[str, Dict[str, str]]]:
    """Return (owner map, team metadata).

    owner map: yahoo_id -> FBP team abbr ("" for unowned).
    team metadata: abbr -> {fbp_abbr, team_name, manager_name}.
    """
    players_2026 = load_json_array(YAHOO_ALL_2026_JSON)
    team_index_2026, abbr_meta = load_team_mappings()

    owner_by_id: Dict[str, str] = {}

    for p in players_2026:
        pid = str(p.get("player_id") or "").strip()
        if not pid:
            continue

        ownership_type = (p.get("ownership_type") or "").strip().lower()
        owned_by = (p.get("owned_by") or "").strip()

        if not owned_by or ownership_type == "freeagents":
            owner_by_id[pid] = ""
            continue

        _team_id, fbp_abbr = decode_team(owned_by, team_index_2026)
        owner_by_id[pid] = fbp_abbr or ""

    print(f"Built 2026 Yahoo owner map for {len(owner_by_id)} player_ids")
    return owner_by_id, abbr_meta


def sync_mlb_owners() -> None:
    yahoo_id_to_upid = load_yahoo_id_to_upid()
    owner_by_yahoo_id, abbr_meta = build_yahoo_2026_owner_map()

    if not COMBINED_PLAYERS_JSON.exists():
        raise SystemExit(f"ERROR: {COMBINED_PLAYERS_JSON} not found")
    with COMBINED_PLAYERS_JSON.open(encoding="utf-8") as f:
        players = json.load(f)

    updated = 0
    mlb_count = 0

    for p in players:
        if (p.get("player_type") or "").strip() != "MLB":
            continue
        mlb_count += 1

        upid = (str(p.get("upid")) if p.get("upid") is not None else "").strip()
        yahoo_id = (str(p.get("yahoo_id")) if p.get("yahoo_id") is not None else "").strip()

        owner_abbr = ""

        # Prefer direct yahoo_id match
        if yahoo_id and yahoo_id in owner_by_yahoo_id:
            owner_abbr = owner_by_yahoo_id[yahoo_id]
        else:
            # Try via UPID → yahoo bridge
            if upid:
                # reverse lookup: yahoo_id_to_upid is yahoo_id -> upid
                # so we scan once to find a yahoo_id that matches this upid
                for yid, u in yahoo_id_to_upid.items():
                    if u == upid:
                        owner_abbr = owner_by_yahoo_id.get(yid, "")
                        break

        old_team = (p.get("FBP_Team") or "").strip()
        new_team = owner_abbr or ""

        if old_team != new_team:
            # Update primary FBP team field
            p["FBP_Team"] = new_team

            if not new_team:
                # Player is now a free agent: clear manager + MLB contract
                p["manager"] = ""
                p["contract_type"] = ""
                p["years_simple"] = ""
                # Status for MLB FAs isn’t currently driven by any other
                # system, so we clear it rather than guessing.
                p["status"] = ""
            else:
                # Player is owned by an FBP team; ensure manager label
                meta = abbr_meta.get(new_team, {})
                team_name = meta.get("team_name") or new_team
                p["manager"] = team_name
            updated += 1

    # Backup
    backup_path = COMBINED_PLAYERS_JSON.with_suffix(".owners_2026_backup.json")
    if not backup_path.exists():
        backup_path.write_text(COMBINED_PLAYERS_JSON.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Wrote backup to {backup_path}")

    with COMBINED_PLAYERS_JSON.open("w", encoding="utf-8") as f:
        json.dump(players, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # Keep the hub copy in sync so the website sees the same data the
    # bot/pipeline are using.
    try:
        HUB_COMBINED_PLAYERS_JSON.write_text(COMBINED_PLAYERS_JSON.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Synced combined_players.json to {HUB_COMBINED_PLAYERS_JSON}")
    except Exception as exc:
        print(f"WARN: failed to sync hub combined_players.json: {exc}")

    print(f"Total MLB players scanned: {mlb_count}")
    print(f"Players with updated FBP_Team from 2026 Yahoo: {updated}")


if __name__ == "__main__":
    sync_mlb_owners()
