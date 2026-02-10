#!/usr/bin/env python3
"""Rebuild data/draft_order_2026.json from existing PAD submissions.

Uses the updated rebuild_draft_order_from_pad() logic so that:
- BC slots give at most one pick per round (round 1 and/or 2).
- DC slots give at most one pick per DC round until slots are exhausted.

This script is safe to run multiple times; it overwrites draft_order_2026.json
from data/pad_submissions_2026.json and managers config, with a backup.
"""

from __future__ import annotations

import json
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pad.pad_processor import rebuild_draft_order_from_pad
DATA_DIR = ROOT / "data"
DRAFT_ORDER_PATH = DATA_DIR / "draft_order_2026.json"
BACKUP_PATH = DATA_DIR / "draft_order_2026_pre_bc_fix_backup.json"


def main() -> None:
    submissions_path = DATA_DIR / "pad_submissions_2026.json"
    if not submissions_path.exists():
        raise SystemExit(f"ERROR: {submissions_path} not found; nothing to rebuild")

    with submissions_path.open(encoding="utf-8") as f:
        submissions = json.load(f)
    if not isinstance(submissions, dict):
        raise SystemExit("pad_submissions_2026.json did not contain an object")

    # Backup current draft_order once.
    if DRAFT_ORDER_PATH.exists() and not BACKUP_PATH.exists():
        BACKUP_PATH.write_text(DRAFT_ORDER_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Wrote backup of existing draft_order_2026.json to {BACKUP_PATH}")

    print(f"Rebuilding draft_order_2026.json from {submissions_path}...")
    rebuild_draft_order_from_pad(submissions, test_mode=False)
    print(f"✅ draft_order_2026.json rebuilt using updated slot logic")


if __name__ == "__main__":
    main()
