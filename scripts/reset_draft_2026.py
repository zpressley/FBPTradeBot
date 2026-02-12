#!/usr/bin/env python3
"""Reset 2026 prospect draft state and clear results in draft_order_2026.json.

Usage (from repo root):

    python scripts/reset_draft_2026.py

This will:
- Delete data/draft_state_prospect_2026.json (so DraftManager will
  re-initialize a fresh state file on next run).
- Set the `result` field on every entry in data/draft_order_2026.json
  back to null.

Safe to run multiple times.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
STATE_PATH = DATA_DIR / "draft_state_prospect_2026.json"
ORDER_PATH = DATA_DIR / "draft_order_2026.json"


def reset_state() -> None:
    if STATE_PATH.exists():
        STATE_PATH.unlink()
        print(f"🗑️  Deleted {STATE_PATH}")
    else:
        print(f"ℹ️  No state file at {STATE_PATH} (nothing to delete)")


def reset_order_results() -> None:
    if not ORDER_PATH.exists():
        print(f"⚠️ {ORDER_PATH} not found; cannot reset results")
        return

    with ORDER_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        picks = data
        container = None
    else:
        picks = data.get("picks") or data.get("rounds") or []
        container = data

    for p in picks:
        # Ensure the key exists and is cleared.
        p["result"] = None

    if container is not None:
        if "picks" in container:
            container["picks"] = picks
        elif "rounds" in container:
            container["rounds"] = picks
        out = container
    else:
        out = picks

    with ORDER_PATH.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    print(f"✅ Cleared results for {len(picks)} picks in {ORDER_PATH}")


def main() -> None:
    print("Resetting 2026 prospect draft state and order results...")
    reset_state()
    reset_order_results()
    print("Done.")


if __name__ == "__main__":
    main()
