"""Export owned prospects with Top 100 rank to CSV.

Owned prospects are sourced from data/combined_players.json.
Top 100 ranks are sourced from data/top100_prospects.json (UPID-based join).

Output:
- scripts/output/owned_prospects_with_top100_rank.csv
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from pad.pad_processor import _load_json  # noqa: E402


COMBINED_PATH = "data/combined_players.json"
TOP100_PATH = "data/top100_prospects.json"
OUT_DIR = "scripts/output"
OUT_PATH = os.path.join(OUT_DIR, "owned_prospects_with_top100_rank.csv")


def _is_owned(rec: dict) -> bool:
    manager = str(rec.get("manager") or "").strip()
    fbp_team = str(rec.get("FBP_Team") or "").strip()

    # Treat either field as ownership signal.
    if fbp_team and fbp_team.lower() != "none":
        return True
    if manager and manager.lower() != "none":
        return True
    return False


def main() -> None:
    combined = _load_json(COMBINED_PATH) or []
    if not isinstance(combined, list):
        raise SystemExit(f"{COMBINED_PATH} is not a list")

    top100 = _load_json(TOP100_PATH) or []
    if not isinstance(top100, list):
        raise SystemExit(f"{TOP100_PATH} is not a list")

    top100_by_upid: dict[str, dict] = {}
    for rec in top100:
        upid = str(rec.get("upid") or "").strip()
        if upid:
            top100_by_upid[upid] = rec

    owned_farm = [p for p in combined if p.get("player_type") == "Farm" and _is_owned(p)]

    # Build output rows
    rows = []
    for p in owned_farm:
        upid = str(p.get("upid") or "").strip()
        t100 = top100_by_upid.get(upid) if upid else None

        top100_rank = t100.get("rank") if t100 else ""
        try:
            top100_rank_int = int(top100_rank) if top100_rank != "" else None
        except Exception:
            top100_rank_int = None

        rows.append(
            {
                "top100_rank": top100_rank_int if top100_rank_int is not None else "",
                "upid": upid,
                "name": p.get("name") or "",
                "FBP_Team": (p.get("FBP_Team") or ""),
                "manager": (p.get("manager") or ""),
                "contract_type": (p.get("contract_type") or ""),
                "org": (p.get("team") or ""),
                "position": (p.get("position") or ""),
                "age": p.get("age") or "",
                "level": (p.get("level") or p.get("mlb_level") or ""),
                "fypd": p.get("fypd") if p.get("fypd") is not None else "",
                "debuted": p.get("debuted") if p.get("debuted") is not None else "",
            }
        )

    # Sort: ranked first by rank asc, then unranked by name
    def _sort_key(r: dict):
        rank = r.get("top100_rank")
        if isinstance(rank, int):
            return (0, rank, str(r.get("name") or ""))
        return (1, 9999, str(r.get("name") or ""))

    rows.sort(key=_sort_key)

    os.makedirs(OUT_DIR, exist_ok=True)

    fieldnames = [
        "top100_rank",
        "upid",
        "name",
        "FBP_Team",
        "manager",
        "contract_type",
        "org",
        "position",
        "age",
        "level",
        "fypd",
        "debuted",
    ]

    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    ranked_count = sum(1 for r in rows if isinstance(r.get("top100_rank"), int))

    print(
        f"Wrote {len(rows)} owned prospects to {OUT_PATH} (ranked in top100: {ranked_count}) at {datetime.now().isoformat()}"
    )


if __name__ == "__main__":
    main()
