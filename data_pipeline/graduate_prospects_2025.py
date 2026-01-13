#!/usr/bin/env python3
"""Graduate prospects at end of 2025 using FBP limits (no service time).

Rules (from FBP Constitution 2026, simplified for automation):

Prospect definition (FBP Limits, MLB-only stats):
- Age 25 & under (turn 26 -> auto graduate)
- Batters: graduate if MLB career >= 350 PA OR >= 80 G
- Pitchers: graduate if MLB career >= 100 IP OR >= 30 G (pitching apps)

Data sources (MLB only):
- Fangraphs hitters:  fangraphs_data/fangraphs-leaderboards (4).csv
- Fangraphs hitters (small samples): fangraphs_data/fangraphs-leaderboards (5).csv
- Fangraphs pitchers: fangraphs_data/fangraphs-leaderboards (8).csv
  (and optionally (9).csv if present)
- Age, positions, contracts, ownership: data/combined_players.json

Graduation effects (end-of-2025 checkpoint):
- If player exceeds FBP limits and is OWNED (manager non-empty):
    * player_type  -> "MLB"
    * contract_type -> "Keeper Contract"
    * status      -> "[6] TCR"
    * years_simple -> "TC R"
    * Player Log: update_type="Graduate", event="25 Rosters"

- If player exceeds FBP limits and is UNOWNED (no manager):
    * player_type  -> "MLB"
    * contract_type -> None
    * status      -> "[5] TC1"
    * years_simple -> "TC 1"
    * Player Log: update_type="Graduate", event="25 Rosters"

Additional cleanup:
- Remove obsolete fields from combined_players.json:
    * service_time_days
    * MLBRookie
- Fix obvious data issues where status / contract_type don't align with
  years_simple + player_type (using years_simple as source of truth).
  Any such fix is logged to player_log with update_type="DataFix",
  event="25 Rosters".

Outputs:
- Updated data/combined_players.json (with timestamped backup).
- Appended data/player_log.json entries for graduates + data-fixes.
- CSV reports under data/historical/2025/:
    * graduates_2025.csv
    * prospect_data_fixes_2025.csv
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from player_log import append_entry  # type: ignore

DATA_DIR = ROOT / "data"
HIST_2025_DIR = DATA_DIR / "historical" / "2025"

COMBINED_PATH = DATA_DIR / "combined_players.json"
FG_HITTERS_MAIN = ROOT / "fangraphs_data" / "fangraphs-leaderboards (4).csv"
FG_HITTERS_SMALL = ROOT / "fangraphs_data" / "fangraphs-leaderboards (5).csv"
FG_PITCHERS_MAIN = ROOT / "fangraphs_data" / "fangraphs-leaderboards (8).csv"
FG_PITCHERS_ALT = ROOT / "fangraphs_data" / "fangraphs-leaderboards (9).csv"
TEAM_MAP_PATH = DATA_DIR / "mlb_team_map.json"

SEASON = 2025
SOURCE = "2025_graduation"
EVENT_LABEL = "25 Rosters"

AGE_LIMIT = 25
BATTER_PA_LIMIT = 350
BATTER_G_LIMIT = 80
PITCHER_IP_LIMIT = 100.0
PITCHER_G_LIMIT = 30


@dataclass
class MLBUsage:
    pa: int = 0
    g_bat: int = 0
    ip: float = 0.0
    g_pitch: int = 0


def load_combined_players() -> List[dict]:
    with COMBINED_PATH.open("r", encoding="utf-8") as f:
        players: List[dict] = json.load(f)

    # Strip obsolete fields
    for p in players:
        p.pop("service_time_days", None)
        p.pop("MLBRookie", None)

    return players


def load_team_aliases() -> set:
    if not TEAM_MAP_PATH.exists():
        return set()
    with TEAM_MAP_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    aliases = set()
    for abbr, info in data.get("official", {}).items():
        aliases.add(abbr.upper())
        for a in info.get("aliases", []):
            aliases.add(str(a).upper())
    for alias, abbr in data.get("aliases", {}).items():
        aliases.add(str(alias).upper())
        aliases.add(str(abbr).upper())
    return aliases


def _load_fg_hitter_file(path: Path, usage: Dict[int, MLBUsage], team_aliases: set) -> None:
    if not path.exists():
        return
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                mlbam = int(row.get("MLBAMID") or 0)
            except ValueError:
                continue
            if not mlbam:
                continue

            team = (row.get("Team") or "").upper().strip()
            if team_aliases and team not in team_aliases:
                # Skip any non-MLB or weird combined rows
                continue

            try:
                g = int(float(row.get("G") or 0))
            except ValueError:
                g = 0
            try:
                pa = int(float(row.get("PA") or row.get("PA ") or 0))
            except ValueError:
                pa = 0

            stat = usage.setdefault(mlbam, MLBUsage())
            stat.pa += pa
            stat.g_bat += g


def _load_fg_pitcher_file(path: Path, usage: Dict[int, MLBUsage], team_aliases: set) -> None:
    if not path.exists():
        return
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                mlbam = int(row.get("MLBAMID") or 0)
            except ValueError:
                continue
            if not mlbam:
                continue

            team = (row.get("Team") or "").upper().strip()
            if team_aliases and team not in team_aliases:
                continue

            try:
                g = int(float(row.get("G") or 0))
            except ValueError:
                g = 0
            ip_raw = (row.get("IP") or "0").strip()
            try:
                ip = float(ip_raw)
            except ValueError:
                ip = 0.0

            stat = usage.setdefault(mlbam, MLBUsage())
            stat.ip += ip
            stat.g_pitch += g


def build_mlb_usage() -> Dict[int, MLBUsage]:
    """Aggregate MLB career usage from Fangraphs CSVs keyed by MLBAMID.

    We sum across all seasons present in the leaderboards; this approximates
    career totals well enough for graduation purposes.
    """

    usage: Dict[int, MLBUsage] = {}
    team_aliases = load_team_aliases()

    _load_fg_hitter_file(FG_HITTERS_MAIN, usage, team_aliases)
    _load_fg_hitter_file(FG_HITTERS_SMALL, usage, team_aliases)
    _load_fg_pitcher_file(FG_PITCHERS_MAIN, usage, team_aliases)
    _load_fg_pitcher_file(FG_PITCHERS_ALT, usage, team_aliases)

    print(f"📊 Aggregated MLB usage for {len(usage)} players from Fangraphs")
    return usage


def is_pitcher(position: str) -> bool:
    parts = [p.strip().upper() for p in (position or "").split(",")]
    return any(p in {"P", "SP", "RP"} for p in parts)


def classify_graduation(player: dict, usage: MLBUsage) -> Tuple[bool, str, Dict[str, float]]:
    """Return (should_graduate, reason, metrics) for a player.

    metrics map is for reporting/logging.
    """

    age = player.get("age")
    metrics = {
        "age": float(age) if isinstance(age, (int, float)) else 0.0,
        "pa": float(usage.pa),
        "g_bat": float(usage.g_bat),
        "ip": float(usage.ip),
        "g_pitch": float(usage.g_pitch),
    }

    if isinstance(age, (int, float)) and age >= AGE_LIMIT + 1:  # turn 26
        return True, f"age>=26 ({age})", metrics

    pos = player.get("position") or ""
    pitcher = is_pitcher(pos)

    if pitcher:
        if usage.ip >= PITCHER_IP_LIMIT:
            return True, f"IP>={PITCHER_IP_LIMIT} ({usage.ip})", metrics
        if usage.g_pitch >= PITCHER_G_LIMIT:
            return True, f"G_pitch>={PITCHER_G_LIMIT} ({usage.g_pitch})", metrics
    else:
        if usage.pa >= BATTER_PA_LIMIT:
            return True, f"PA>={BATTER_PA_LIMIT} ({usage.pa})", metrics
        if usage.g_bat >= BATTER_G_LIMIT:
            return True, f"G_bat>={BATTER_G_LIMIT} ({usage.g_bat})", metrics

    return False, "", metrics


def canonical_status_from_years(years: str) -> Optional[str]:
    y = (years or "").strip().upper()
    mapping = {
        "FC 2": "[0] FC2",
        "FC 1": "[1] FC1",
        "VC 2": "[2] VC2",
        "VC 1": "[3] VC1",
        "TC 2": "[4] TC2",
        "TC 1": "[5] TC1",
        "TC R": "[6] TCR",
        "P": "[7] P",
        "TC-BC1": "[6] TC-BC1",
        "TC-BC2": "[6] TC-BC2",
    }
    return mapping.get(y)


def fix_data_issues(players: List[dict]) -> List[dict]:
    """Normalize status/contract_type for consistency.

    Returns list of players that were updated.
    """

    updated: List[dict] = []

    for p in players:
        before = {
            "status": p.get("status"),
            "contract_type": p.get("contract_type"),
        }
        years = p.get("years_simple") or ""
        player_type = (p.get("player_type") or "").strip()

        # Fix status based on years_simple when possible.
        canonical = canonical_status_from_years(years)
        if canonical and p.get("status") != canonical:
            p["status"] = canonical

        # If MLB player is still carrying a clearly "prospect" contract
        # description, clear it so contracts are driven by years_simple.
        if player_type == "MLB" and p.get("contract_type") in {
            "Development Cont.",
            "Farm Contract",
            "Purchased Contract",
        }:
            p["contract_type"] = None

        if {
            "status": p.get("status"),
            "contract_type": p.get("contract_type"),
        } != before:
            updated.append(p)

    return updated


def graduate_prospects() -> None:
    players = load_combined_players()
    usage_map = build_mlb_usage()

    graduates: List[dict] = []
    data_fixes: List[dict] = []

    # Index for quick MLB usage lookup by mlb_id
    mlb_usage_by_id: Dict[int, MLBUsage] = usage_map

    # First pass: identify and apply graduations
    for p in players:
        years = (p.get("years_simple") or "").strip().upper()
        player_type = (p.get("player_type") or "").strip()

        # Only consider players with prospect-like years_simple and/or Farm type.
        if years != "P" and player_type != "FARM":
            continue

        mlb_id = p.get("mlb_id")
        if not isinstance(mlb_id, int):
            continue

        usage = mlb_usage_by_id.get(mlb_id, MLBUsage())
        should_grad, reason, metrics = classify_graduation(p, usage)
        if not should_grad:
            continue

        owned = bool((p.get("manager") or "").strip())

        before = {
            "player_type": p.get("player_type"),
            "contract_type": p.get("contract_type"),
            "status": p.get("status"),
            "years_simple": p.get("years_simple"),
        }

        # Determine new contract based on ownership.
        if owned:
            p["player_type"] = "MLB"
            p["contract_type"] = "Keeper Contract"
            p["status"] = "[6] TCR"
            p["years_simple"] = "TC R"
        else:
            p["player_type"] = "MLB"
            p["contract_type"] = None
            p["status"] = "[5] TC1"
            p["years_simple"] = "TC 1"

        graduates.append({
            "upid": p.get("upid"),
            "name": p.get("name"),
            "manager": p.get("manager") or "",
            "owned": owned,
            "mlb_id": mlb_id,
            "reason": reason,
            **metrics,
            "before_player_type": before["player_type"],
            "before_contract_type": before["contract_type"],
            "before_status": before["status"],
            "before_years": before["years_simple"],
            "after_player_type": p["player_type"],
            "after_contract_type": p["contract_type"],
            "after_status": p["status"],
            "after_years": p["years_simple"],
        })

        # Log to player_log
        append_entry(
            season=SEASON,
            source=SOURCE,
            upid=str(p.get("upid") or ""),
            player_name=p.get("name") or "",
            team=p.get("team") or "",
            pos=p.get("position") or "",
            age=p.get("age"),
            level=str(p.get("level") or ""),
            team_rank=p.get("team_rank"),
            rank=p.get("rank"),
            eta=str(p.get("eta") or ""),
            player_type=p.get("player_type") or "",
            owner=p.get("manager") or "",
            contract=p.get("contract_type") or "",
            status=p.get("status") or "",
            years=p.get("years_simple") or "",
            update_type="Graduate",
            event=EVENT_LABEL,
            admin="2025_graduation",
        )

    # Second pass: fix data issues across all players
    fixed_players = fix_data_issues(players)
    for p in fixed_players:
        data_fixes.append({
            "upid": p.get("upid"),
            "name": p.get("name"),
            "manager": p.get("manager") or "",
            "player_type": p.get("player_type"),
            "contract_type": p.get("contract_type"),
            "status": p.get("status"),
            "years_simple": p.get("years_simple"),
        })

        append_entry(
            season=SEASON,
            source=SOURCE,
            upid=str(p.get("upid") or ""),
            player_name=p.get("name") or "",
            team=p.get("team") or "",
            pos=p.get("position") or "",
            age=p.get("age"),
            level=str(p.get("level") or ""),
            team_rank=p.get("team_rank"),
            rank=p.get("rank"),
            eta=str(p.get("eta") or ""),
            player_type=p.get("player_type") or "",
            owner=p.get("manager") or "",
            contract=p.get("contract_type") or "",
            status=p.get("status") or "",
            years=p.get("years_simple") or "",
            update_type="DataFix",
            event=EVENT_LABEL,
            admin="2025_graduation",
        )

    # Backup + save combined_players
    backup_path = COMBINED_PATH.with_name("combined_players_backup_2025_graduates.json")
    with backup_path.open("w", encoding="utf-8") as bf:
        json.dump(players, bf, indent=2)
    with COMBINED_PATH.open("w", encoding="utf-8") as f:
        json.dump(players, f, indent=2)

    print(f"📦 Backup written to {backup_path}")
    print(f"💾 Saved updated combined players to {COMBINED_PATH}")

    # Reports
    HIST_2025_DIR.mkdir(parents=True, exist_ok=True)

    grads_csv = HIST_2025_DIR / "graduates_2025.csv"
    if graduates:
        with grads_csv.open("w", newline="", encoding="utf-8") as f:
            fieldnames = list(graduates[0].keys())
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(graduates)
        print(f"📄 Graduates report → {grads_csv} ({len(graduates)} rows)")
    else:
        print("📄 No graduates identified; graduates report not written")

    fixes_csv = HIST_2025_DIR / "prospect_data_fixes_2025.csv"
    if data_fixes:
        with fixes_csv.open("w", newline="", encoding="utf-8") as f:
            fieldnames = list(data_fixes[0].keys())
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data_fixes)
        print(f"📄 Data-fix report → {fixes_csv} ({len(data_fixes)} rows)")
    else:
        print("📄 No data issues fixed; data-fix report not written")

    print("\n✅ Graduation pass complete:")
    print(f"   Graduates:   {len(graduates)}")
    print(f"   Data fixes:  {len(data_fixes)}")


if __name__ == "__main__":
    graduate_prospects()
