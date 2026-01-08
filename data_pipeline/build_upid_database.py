#!/usr/bin/env python3
"""Build UPID database and MLB team alias map from the original Google Sheet.

Source sheet:
- Doc key: 19hH-bUVbtbF4Qn4Ep6YRCK853eOvoI8lr2zNlRB1wgo
- UPID data:    worksheet 'PlayerUpid', starting row 2
  - Col A: Player Name
  - Col B: Team
  - Col C: POS
  - Col D: UPID
  - Col E: Alternate Name 1
  - Col F: Alternate Name 2
  - Col G: Alternate Name 3 (No Accents)
  - Col I: Approved Dupes (notes about known name duplicates)

- MLB team mapper: worksheet 'ADMIN', range L1:Q31
  - Col L: MLB Official (canonical team name)
  - Col M-Q: alternate representations / aliases

Outputs (written under data/):
- data/upid_database.json
    {
      "by_upid": {
        "1921": {
          "upid": "1921",
          "name": "Tim Anderson",
          "team": "CWS",
          "pos": "SS",
          "alt_names": ["Tim Anderson Jr", "..."],
          "approved_dupes": "raw text from sheet"
        },
        ...
      },
      "name_index": {
        "tim anderson": ["1921"],
        "tim anderson jr": ["1921"],
        ...
      }
    }

- data/mlb_team_map.json
    {
      "official": {
        "Seattle Mariners": {"aliases": ["SEA", "Mariners", ...]},
        ...
      },
      "aliases": {
        "sea": "Seattle Mariners",
        "mariners": "Seattle Mariners",
        ...
      }
    }

This script uses the same google_creds.json service account used by other
pipeline scripts.
"""

import json
import os
from typing import Any, Dict, List

import gspread
from oauth2client.service_account import ServiceAccountCredentials

SHEET_KEY = "19hH-bUVbtbF4Qn4Ep6YRCK853eOvoI8lr2zNlRB1wgo"
UPID_SHEET_NAME = "PlayerUPID"
ADMIN_SHEET_NAME = "ADMIN"

UPID_OUTPUT_PATH = os.path.join("data", "upid_database.json")
TEAM_MAP_OUTPUT_PATH = os.path.join("data", "mlb_team_map.json")


def get_client() -> gspread.Client:
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name("google_creds.json", scope)
    return gspread.authorize(creds)


def build_upid_database(sheet: gspread.Worksheet) -> Dict[str, Any]:
    """Read PlayerUpid tab and build structured UPID database.

    We read all values and interpret columns by position so header text
    changes do not break the script.
    """

    values = sheet.get_all_values()
    if not values:
        raise SystemExit("PlayerUpid sheet is empty")

    header = values[0]
    rows = values[1:]  # data starts on row 2

    # Column indices (0-based) based on your description
    COL_NAME = 0  # A
    COL_TEAM = 1  # B
    COL_POS = 2   # C
    COL_UPID = 3  # D
    COL_ALT1 = 4  # E
    COL_ALT2 = 5  # F
    COL_ALT3 = 6  # G (no accents)
    COL_APPROVED_DUPES = 8  # I

    by_upid: Dict[str, Dict[str, Any]] = {}
    name_index: Dict[str, List[str]] = {}

    def add_name_index(name: str, upid: str) -> None:
        key = name.strip().lower()
        if not key:
            return
        name_index.setdefault(key, []).append(upid)

    for row in rows:
        # Pad short rows so indexing is safe
        if len(row) <= COL_NAME:
            continue
        row = row + [""] * (COL_APPROVED_DUPES + 1 - len(row))

        name = row[COL_NAME].strip()
        team = row[COL_TEAM].strip()
        pos = row[COL_POS].strip()
        upid = row[COL_UPID].strip()

        if not upid and not name:
            continue
        if not upid:
            # Skip rows without a UPID; they aren't part of the canonical index
            continue

        alt_names_raw = [row[COL_ALT1].strip(), row[COL_ALT2].strip(), row[COL_ALT3].strip()]
        alt_names = [a for a in alt_names_raw if a]
        approved_dupes = row[COL_APPROVED_DUPES].strip() if len(row) > COL_APPROVED_DUPES else ""

        rec = {
            "upid": upid,
            "name": name,
            "team": team,
            "pos": pos,
            "alt_names": alt_names,
            "approved_dupes": approved_dupes,
        }

        by_upid[upid] = rec

        # Index primary + alternate names
        if name:
            add_name_index(name, upid)
        for alt in alt_names:
            add_name_index(alt, upid)

    return {"by_upid": by_upid, "name_index": name_index}


def build_team_map(sheet: gspread.Worksheet) -> Dict[str, Any]:
    """Read ADMIN!L1:Q31 and build MLB team alias map.

    L: MLB Official
    M-Q: alternate forms. We treat every non-empty cell as an alias.
    """

    # Fetch the explicit range so we don't depend on other ADMIN content.
    values = sheet.get("L1:Q31")
    if not values:
        raise SystemExit("ADMIN!L1:Q31 is empty")

    # First row is expected to be headers; skip it if so.
    data_rows = values[1:] if values and values[0] and values[0][0].strip().lower() == "mlb official" else values

    official: Dict[str, Dict[str, Any]] = {}
    alias_map: Dict[str, str] = {}

    for row in data_rows:
        if not row:
            continue
        # Pad row to length 6 (L..Q)
        row = row + [""] * (6 - len(row))
        official_name = row[0].strip()
        if not official_name:
            continue

        aliases = [c.strip() for c in row[1:] if c and c.strip() and c.strip() != "-"]

        # Record official -> aliases
        entry = official.setdefault(official_name, {"aliases": []})
        for alias in aliases:
            if alias not in entry["aliases"]:
                entry["aliases"].append(alias)

        # Record alias -> official (lowercased for case-insensitive lookup)
        for alias in aliases:
            key = alias.strip().lower()
            if not key:
                continue
            alias_map[key] = official_name

        # Also index the official name itself as an alias for convenience
        alias_map.setdefault(official_name.strip().lower(), official_name)

    return {"official": official, "aliases": alias_map}


def save_json(obj: Dict[str, Any], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print(f"✅ Wrote {path}")


def main() -> None:
    client = get_client()
    doc = client.open_by_key(SHEET_KEY)

    print("📄 Loading PlayerUpid sheet...")
    upid_ws = doc.worksheet(UPID_SHEET_NAME)
    upid_db = build_upid_database(upid_ws)
    save_json(upid_db, UPID_OUTPUT_PATH)

    print("\n📄 Loading ADMIN sheet for MLB team map...")
    admin_ws = doc.worksheet(ADMIN_SHEET_NAME)
    team_map = build_team_map(admin_ws)
    save_json(team_map, TEAM_MAP_OUTPUT_PATH)


if __name__ == "__main__":
    main()
