"""Update data/top100_prospects.json from a CSV.

Input CSV expectations (0-indexed columns):
- col 0: UPID
- col 1: Rank
- col 2: Player name
- col 3: POS
- col 4: Team (org)
- col 5: Level
- col 6: ETA
- col 7: AGE
- col 9: Bats (often unlabeled)
- col 10: Throws (often unlabeled)

Join enrichment:
- data/combined_players.json (by UPID) for mlb_id, yahoo_id, FBP_Team

Behavior:
- Removes anyone not present in the CSV.
- Creates a backup under data/Backups/.

Usage:
  python3 scripts/update_top100_prospects_from_csv.py \
    --csv "/Users/zpressley/Downloads/2025 ownership - Sheet11.csv"
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from datetime import datetime

# Allow importing repo modules when running from scripts/.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from pad.pad_processor import _load_json, _save_json


TOP100_PATH = "data/top100_prospects.json"
COMBINED_PATH = "data/combined_players.json"
BACKUP_DIR = "data/Backups"


def _backup(path: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    out = os.path.join(BACKUP_DIR, os.path.basename(path) + f".bak_{ts}")
    shutil.copy2(path, out)
    return out


def _cell(row: list[str], idx: int) -> str:
    if idx < 0 or idx >= len(row):
        return ""
    return str(row[idx] or "").strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="Path to the ownership/top100 CSV")
    args = ap.parse_args()

    csv_path = args.csv
    if not os.path.exists(csv_path):
        raise SystemExit(f"CSV not found: {csv_path}")

    combined = _load_json(COMBINED_PATH) or []
    if not isinstance(combined, list):
        raise SystemExit(f"{COMBINED_PATH} is not a list")

    combined_by_upid: dict[str, dict] = {}
    for rec in combined:
        upid = str(rec.get("upid") or "").strip()
        if upid:
            combined_by_upid[upid] = rec

    rows: list[dict] = []
    missing_upids: list[str] = []

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if not row:
                continue

            # Header row
            if i == 0 and _cell(row, 0).upper() == "UPID":
                continue

            upid = _cell(row, 0)
            rank_raw = _cell(row, 1)
            if not upid or not rank_raw:
                continue

            try:
                rank = int(rank_raw)
            except Exception:
                continue

            name = _cell(row, 2)
            pos = _cell(row, 3)
            org = _cell(row, 4)
            level = _cell(row, 5)
            eta = _cell(row, 6)
            age = _cell(row, 7)
            bats = _cell(row, 9)
            throws = _cell(row, 10)

            combined_rec = combined_by_upid.get(upid)
            if not combined_rec:
                missing_upids.append(upid)

            mlb_id = combined_rec.get("mlb_id") if combined_rec else None
            yahoo_id = combined_rec.get("yahoo_id") if combined_rec else ""
            fbp_team = (combined_rec.get("FBP_Team") if combined_rec else "") or ""

            rows.append(
                {
                    "rank": rank,
                    "name": name or (combined_rec.get("name") if combined_rec else ""),
                    "position": pos or (combined_rec.get("position") if combined_rec else ""),
                    "org": org or (combined_rec.get("team") if combined_rec else ""),
                    "level": level,
                    "eta": eta,
                    "age": str(age) if age != "" else "",
                    "bats": bats,
                    "throws": throws,
                    "mlb_id": mlb_id,
                    "upid": upid,
                    "yahoo_id": yahoo_id,
                    "FBP_Team": str(fbp_team).strip(),
                }
            )

    if not rows:
        raise SystemExit("No usable rows found in CSV")

    # Validate / de-dupe by UPID (keep best/lowest rank)
    best_by_upid: dict[str, dict] = {}
    for r in rows:
        u = r.get("upid")
        if not u:
            continue
        existing = best_by_upid.get(u)
        if existing is None or (isinstance(r.get("rank"), int) and r["rank"] < existing.get("rank", 9999)):
            best_by_upid[u] = r

    out_rows = list(best_by_upid.values())
    out_rows.sort(key=lambda r: int(r.get("rank") or 9999))

    if missing_upids:
        # Not fatal, but important to know.
        print(f"⚠️ {len(set(missing_upids))} UPID(s) from CSV missing in combined_players.json")

    if not os.path.exists(TOP100_PATH):
        raise SystemExit(f"Missing {TOP100_PATH}")

    backup = _backup(TOP100_PATH)
    _save_json(TOP100_PATH, out_rows)

    print(
        json.dumps(
            {
                "ok": True,
                "csv_rows": len(rows),
                "unique_players": len(out_rows),
                "backup": backup,
                "output": TOP100_PATH,
                "missing_upids_in_combined": len(set(missing_upids)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
