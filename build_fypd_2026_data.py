#!/usr/bin/env python3
"""Build FYPD 2026 data for the draft system.

Logical flow:
1. Read the UPID Google Sheet (PlayerUPID tab) to build an in-memory UPID database.
2. Read the FYPD 2026 CSV and build a structured list/JSON of FYPD players.
3. Add/update FYPD players in data/combined_players.json as unowned Farm prospects.
4. Emit data/fypd_2026_rankings.json for draft tooling (round 1–2 pool + metadata).

This script is designed to be idempotent: re-running it will update the same
set of players in combined_players.json and overwrite fypd_2026_rankings.json.

Requirements:
- google_creds.json present (same service account used by other pipeline scripts).
- gspread + oauth2client installed (already used elsewhere in this repo).
"""

import csv
import json
import os
import sys
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

from data_pipeline.build_upid_database import (
    SHEET_KEY as UPID_SHEET_KEY,
    UPID_SHEET_NAME,
    build_upid_database,
    get_client,
)


FYPD_CSV_PATH = os.path.join("data", "historical", "2025", "FYPD 2026 - FYPD.csv")
COMBINED_PLAYERS_PATH = os.path.join("data", "combined_players.json")
MLB_ID_CACHE_PATH = os.path.join("data", "mlb_id_cache.json")
FYPD_JSON_OUTPUT = os.path.join("data", "fypd_2026_rankings.json")
COMBINED_BACKUP_PATH = os.path.join("data", "combined_players_fypd_2026_backup.json")


@dataclass
class FypdPlayer:
    upid: str
    rank: int
    name: str
    mlb_team: str
    position: str
    school: str
    level: str
    eta: str
    age: Optional[int]
    height_weight: str
    bats: str
    throws: str
    mlb_id: Optional[int] = None


def load_upid_database() -> Dict[str, Dict[str, Any]]:
    """Load the UPID database directly from the Google Sheet.

    Returns a mapping of upid -> record as produced by build_upid_database.
    """

    print("\n📄 Loading UPID database from Google Sheet...")
    client = get_client()
    doc = client.open_by_key(UPID_SHEET_KEY)
    ws = doc.worksheet(UPID_SHEET_NAME)
    upid_db = build_upid_database(ws)
    by_upid: Dict[str, Dict[str, Any]] = upid_db["by_upid"]
    print(f"   ✅ Loaded {len(by_upid)} UPID records from sheet")
    return by_upid


def load_upid_mlb_id_map() -> Dict[str, int]:
    """Load UPID -> MLB ID mapping directly from the UPID sheet.

    This is used to fill in MLB IDs for new FYPD players that are not yet
    present in data/mlb_id_cache.json, so that the website has full IDs.
    """

    print("\n📄 Loading MLB IDs from UPID sheet (PlayerUPID tab)...")
    client = get_client()
    doc = client.open_by_key(UPID_SHEET_KEY)
    ws = doc.worksheet(UPID_SHEET_NAME)
    all_data = ws.get_all_values()

    if len(all_data) < 3:
        print("   ⚠️  UPID sheet too short to contain MLB ID data; skipping sheet MLB IDs")
        return {}

    # In the master sheet, headers with MLB ID live on the second row (index 1).
    headers = all_data[1]

    # UPID is column D (index 3) in the PlayerUPID tab.
    upid_idx = 3

    mlb_id_idx: Optional[int] = None
    for i, header in enumerate(headers):
        if "mlb" in header.lower() and "id" in header.lower():
            mlb_id_idx = i
            break

    if mlb_id_idx is None:
        print("   ⚠️  Could not find an MLB ID column in PlayerUPID headers; skipping sheet MLB IDs")
        return {}

    mapping: Dict[str, int] = {}
    for row in all_data[2:]:  # data starts on row 3
        if len(row) <= upid_idx:
            continue
        upid_val = str(row[upid_idx]).strip()
        if not upid_val:
            continue

        mlb_id_val = row[mlb_id_idx].strip() if mlb_id_idx < len(row) else ""
        if not mlb_id_val or not mlb_id_val.isdigit():
            continue

        mapping[upid_val] = int(mlb_id_val)

    print(f"   ✅ Loaded {len(mapping)} UPID -> MLB ID entries from sheet")
    return mapping


def load_mlb_id_cache() -> Dict[str, Dict[str, Any]]:
    """Load MLB ID cache keyed by UPID, if available.

    Missing file or malformed entries are treated gracefully.
    """

    if not os.path.exists(MLB_ID_CACHE_PATH):
        print(f"\n⚠️  MLB ID cache not found at {MLB_ID_CACHE_PATH}; proceeding without it")
        return {}

    with open(MLB_ID_CACHE_PATH, "r") as f:
        raw = json.load(f)

    cache: Dict[str, Dict[str, Any]] = {}
    for upid, rec in raw.items():
        # Expect {"name": str, "mlb_id": int}
        if not isinstance(rec, dict):
            continue
        mlb_id = rec.get("mlb_id")
        if isinstance(mlb_id, int):
            cache[str(upid)] = {"name": rec.get("name", ""), "mlb_id": mlb_id}
    print(f"\n📦 MLB ID cache: {len(cache)} entries with MLB IDs")
    return cache


def _parse_int(value: str) -> Optional[int]:
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def load_fypd_players(
    upid_db: Dict[str, Dict[str, Any]],
    mlb_id_cache: Dict[str, Dict[str, Any]],
    upid_mlb_map: Dict[str, int],
) -> List[FypdPlayer]:
    """Read the FYPD CSV and build a list of FypdPlayer objects.

    Also cross-checks UPIDs against the live UPID sheet data and attaches
    mlb_id from mlb_id_cache when possible.
    """

    if not os.path.exists(FYPD_CSV_PATH):
        raise SystemExit(f"FYPD CSV not found at {FYPD_CSV_PATH}")

    print(f"\n📄 Loading FYPD CSV from {FYPD_CSV_PATH}...")
    players: List[FypdPlayer] = []

    with open(FYPD_CSV_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing_upids: List[Tuple[str, str]] = []
        name_mismatches: List[Tuple[str, str, str]] = []

        for row in reader:
            upid = row.get("UPID", "").strip()
            name = row.get("Player", "").strip()
            if not upid:
                print(f"   ⚠️  Skipping row with missing UPID for player {name!r}")
                continue

            rank_str = row.get("Rank", "").strip()
            rank = _parse_int(rank_str)
            if rank is None:
                print(f"   ⚠️  Skipping UPID {upid} / player {name!r}: invalid Rank {rank_str!r}")
                continue

            mlb_team = row.get("Team", "").strip()
            position = row.get("Position", "").strip()
            school = row.get("School", "").strip()
            level = row.get("Level", "").strip()
            eta = row.get("eta", "").strip()
            age = _parse_int(row.get("Age", ""))
            height_weight = row.get("Height / Weight", "").strip()
            bats = row.get("Bats", "").strip()
            throws = row.get("Throws", "").strip()

            # Cross-check against UPID sheet
            rec = upid_db.get(upid)
            if rec is None:
                missing_upids.append((upid, name))
            else:
                sheet_name = rec.get("name", "")
                if sheet_name and sheet_name.lower() != name.lower():
                    name_mismatches.append((upid, sheet_name, name))

            mlb_id = None
            cache_rec = mlb_id_cache.get(upid)
            if cache_rec is not None and isinstance(cache_rec.get("mlb_id"), int):
                mlb_id = cache_rec["mlb_id"]

            # Fall back to MLB ID from the UPID sheet if the cache is missing it.
            if mlb_id is None:
                sheet_mlb_id = upid_mlb_map.get(upid)
                if isinstance(sheet_mlb_id, int):
                    mlb_id = sheet_mlb_id

            players.append(
                FypdPlayer(
                    upid=upid,
                    rank=rank,
                    name=name,
                    mlb_team=mlb_team,
                    position=position,
                    school=school,
                    level=level,
                    eta=eta,
                    age=age,
                    height_weight=height_weight,
                    bats=bats,
                    throws=throws,
                    mlb_id=mlb_id,
                )
            )

    print(f"   ✅ Loaded {len(players)} FYPD players from CSV")

    if missing_upids:
        print("\n⚠️  FYPD UPIDs missing from UPID sheet (check Google sheet vs CSV):")
        for upid, name in missing_upids[:10]:
            print(f"     - UPID {upid}: {name}")
        if len(missing_upids) > 10:
            print(f"     ... and {len(missing_upids) - 10} more")

    if name_mismatches:
        print("\n⚠️  Name mismatches between UPID sheet and FYPD CSV:")
        for upid, sheet_name, csv_name in name_mismatches[:10]:
            print(f"     - UPID {upid}: sheet={sheet_name!r}, csv={csv_name!r}")
        if len(name_mismatches) > 10:
            print(f"     ... and {len(name_mismatches) - 10} more")

    return players


def load_combined_players() -> List[Dict[str, Any]]:
    if not os.path.exists(COMBINED_PLAYERS_PATH):
        raise SystemExit(f"combined_players.json not found at {COMBINED_PLAYERS_PATH}")

    with open(COMBINED_PLAYERS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise SystemExit("combined_players.json is not a list; unexpected format")

    print(f"\n📄 Loaded {len(data)} players from combined_players.json")
    return data


def backup_combined_players(players: List[Dict[str, Any]]) -> None:
    """Write a one-time backup of combined_players.json before mutation."""

    if os.path.exists(COMBINED_BACKUP_PATH):
        print(f"\nℹ️  Backup already exists at {COMBINED_BACKUP_PATH}; not overwriting")
        return

    with open(COMBINED_BACKUP_PATH, "w", encoding="utf-8") as f:
        json.dump(players, f, indent=2, ensure_ascii=False)
    print(f"\n💾 Wrote backup of combined_players.json to {COMBINED_BACKUP_PATH}")


def apply_fypd_to_combined(
    combined: List[Dict[str, Any]],
    fypd_players: List[FypdPlayer],
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Add/update FYPD players in combined_players.json.

    Returns (updated_combined, num_updated, num_created).
    """

    by_upid: Dict[str, Dict[str, Any]] = {}
    for p in combined:
        upid = str(p.get("upid", "")).strip()
        if upid:
            by_upid[upid] = p

    updated = 0
    created = 0

    for fp in fypd_players:
        existing = by_upid.get(fp.upid)
        base_record = asdict(fp)

        # Core combined_players fields we care about
        common_fields: Dict[str, Any] = {
            "name": fp.name,
            "team": fp.mlb_team or "",
            "position": fp.position or "",
            "manager": None,
            "player_type": "Farm",
            "contract_type": None,
            "status": "[7] P",
            "years_simple": "P",
            "yahoo_id": "",
            "upid": fp.upid,
            "mlb_id": fp.mlb_id,
            "FBP_Team": "",
        }

        # Optionally include a few extra metadata fields if present
        if fp.age is not None:
            common_fields["age"] = fp.age
        if fp.height_weight:
            common_fields["height_weight"] = fp.height_weight
        if fp.bats:
            common_fields["bats"] = fp.bats
        if fp.throws:
            common_fields["throws"] = fp.throws

        if existing is not None:
            # Update in place but do not nuke unrelated keys (like birth_date, etc.)
            existing.update(common_fields)
            updated += 1
        else:
            # New combined_players entry for this FYPD prospect
            record: Dict[str, Any] = common_fields.copy()
            combined.append(record)
            by_upid[fp.upid] = record
            created += 1

    print(
        f"\n🛠️  Applied FYPD players to combined_players.json: "
        f"{updated} updated, {created} created"
    )
    return combined, updated, created


def mark_fypd_flag_on_combined(
    combined: List[Dict[str, Any]],
    fypd_players: List[FypdPlayer],
    field_name: str = "fypd",
) -> List[Dict[str, Any]]:
    """Mark each combined_players record with a boolean FYPD flag.

    - True if the player's UPID appears in the current FYPD rankings.
    - False otherwise (explicitly set so consumers can rely on the field).
    """

    fypd_upids = {fp.upid for fp in fypd_players}

    for rec in combined:
        upid = str(rec.get("upid", "")).strip()
        # Set the generic FYPD flag
        rec[field_name] = bool(upid and upid in fypd_upids)
        # Clean up old versioned flag if present
        if "is_fypd_2026" in rec:
            rec.pop("is_fypd_2026", None)

    print(
        f"\n🏷️  Marked {len(combined)} players with {field_name} flag "
        f"({len(fypd_upids)} FYPD UPIDs)"
    )
    return combined


def write_combined_players(players: List[Dict[str, Any]]) -> None:
    with open(COMBINED_PLAYERS_PATH, "w", encoding="utf-8") as f:
        json.dump(players, f, indent=2, ensure_ascii=False)
    print(f"\n✅ Wrote updated combined_players.json ({len(players)} total players)")


def write_fypd_rankings(fypd_players: List[FypdPlayer]) -> None:
    payload = {
        "season": 2026,
        "source_csv": FYPD_CSV_PATH,
        "players": [
            {
                "upid": fp.upid,
                "rank": fp.rank,
                "name": fp.name,
                "mlb_team": fp.mlb_team,
                "position": fp.position,
                "school": fp.school,
                "level": fp.level,
                "eta": fp.eta,
                "age": fp.age,
                "height_weight": fp.height_weight,
                "bats": fp.bats,
                "throws": fp.throws,
                "mlb_id": fp.mlb_id,
            }
            for fp in sorted(fypd_players, key=lambda p: p.rank)
        ],
    }

    with open(FYPD_JSON_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(
        f"\n✅ Wrote FYPD rankings JSON for 2026 to {FYPD_JSON_OUTPUT} "
        f"({len(payload['players'])} players)"
    )


def main() -> None:
    print("🚀 Building FYPD 2026 data (UPID -> FYPD -> combined_players)...")

    # 1) Read UPID Google sheet
    upid_db = load_upid_database()

    # 2) Load MLB ID cache (for mlb_id enrichment)
    mlb_id_cache = load_mlb_id_cache()

    # 2.0) Load UPID-sheet MLB ID mapping to fill gaps not in the cache
    upid_mlb_map = load_upid_mlb_id_map()

    # 2.1) Read FYPD players CSV -> structured list
    fypd_players = load_fypd_players(upid_db, mlb_id_cache, upid_mlb_map)

    # 3) Add/update FYPD players in combined_players.json
    combined = load_combined_players()
    backup_combined_players(combined)
    combined_updated, _, _ = apply_fypd_to_combined(combined, fypd_players)

    # 3.a) Mark a boolean flag on every combined_players entry indicating
    # whether they are part of the 2026 FYPD rankings.
    combined_flagged = mark_fypd_flag_on_combined(combined_updated, fypd_players)
    write_combined_players(combined_flagged)

    # 4) Emit rankings JSON for draft tooling
    write_fypd_rankings(fypd_players)

    print("\n🎯 Done. FYPD 2026 data is ready.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user", file=sys.stderr)
        sys.exit(1)
