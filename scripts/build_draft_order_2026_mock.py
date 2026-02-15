#!/usr/bin/env python3
"""
Builds data/draft_order_2026_mock.json by overlaying keeper picks onto the
prospect draft base file (data/draft_order_2026.json) without modifying it.

Rules:
- DO NOT edit data/draft_order_2026.json (sensitive; used by prospect draft)
- Keeper picks are stored in data/keeper_draft_picks_2026.json
- When merging, append keeper picks to the end of the base list
- For keeper picks, round_type is inferred: rounds 1-3 => 'VC', rounds 4+ => 'TC'

Usage:
  python scripts/build_draft_order_2026_mock.py

Output:
  data/draft_order_2026_mock.json
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BASE_FILE = DATA / "draft_order_2026.json"
KEEPER_FILE = DATA / "keeper_draft_picks_2026.json"
OUT_FILE = DATA / "draft_order_2026_mock.json"


def load_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if data is not None else default
    except FileNotFoundError:
        return default


def normalize_keeper_pick(rec: dict) -> dict:
    """Ensure keeper pick has required fields and inferred round_type."""
    out = dict(rec)
    out.setdefault("draft", "keeper")
    rnd = int(out.get("round", 0) or 0)
    # Round types: 1-3 => VC; 4+ => TC
    out["round_type"] = "VC" if 1 <= rnd <= 3 else "TC"
    # Result block normalization
    res = out.get("result") or {}
    if not isinstance(res, dict):
        res = {}
    out["result"] = {
        "player": res.get("player"),
        "timestamp": res.get("timestamp"),
        "pick_index": res.get("pick_index"),
        "upid": res.get("upid"),
    }
    return out


def main() -> int:
    base = load_json(BASE_FILE, default=[])
    keepers = load_json(KEEPER_FILE, default=[])

    if not isinstance(base, list):
        print("❌ Base draft_order_2026.json is not a list; aborting.")
        return 1
    if not isinstance(keepers, list):
        print("❌ keeper_draft_picks_2026.json is not a list; aborting.")
        return 1

    normalized = [normalize_keeper_pick(x) for x in keepers if isinstance(x, dict)]

    combined = list(base) + normalized  # append-only per instruction

    OUT_FILE.write_text(json.dumps(combined, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"✅ Wrote {OUT_FILE.relative_to(ROOT)}")
    print(f"   Base picks:   {len(base)}")
    print(f"   Keeper picks: {len(normalized)} (appended)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
