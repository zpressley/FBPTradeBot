"""
FBP Data Backup

Two modes:
  --baseline   Create a one-time post-draft snapshot in data/backups/post_draft_2026/
  --daily      Rolling backup of previous day's data in data/backups/previous_day/
               (overwrites each run so only the last day is kept)

player_log.json is excluded — it is its own audit trail.
"""

import json
import os
import shutil
import sys
from datetime import datetime

BACKUP_ROOT = "data/backups"
BASELINE_DIR = os.path.join(BACKUP_ROOT, "post_draft_2026")
DAILY_DIR = os.path.join(BACKUP_ROOT, "previous_day")

# Key data files worth backing up
FILES_TO_BACKUP = [
    "data/combined_players.json",
    "data/draft_order_2026.json",
    "data/manager_boards_2026.json",
    "data/wizbucks.json",
    "data/draft_state_keeper_2026.json",
    "data/draft_state_prospect_2026.json",
    "data/trades.json",
    "config/managers.json",
]


def backup_files(dest_dir: str, label: str) -> int:
    """Copy key data files into dest_dir. Returns count of files copied."""
    os.makedirs(dest_dir, exist_ok=True)
    copied = 0
    for src in FILES_TO_BACKUP:
        if not os.path.exists(src):
            continue
        dst = os.path.join(dest_dir, os.path.basename(src))
        shutil.copy2(src, dst)
        copied += 1

    # Write a small manifest so we know when this backup was created
    manifest = {
        "label": label,
        "created_at": datetime.now().isoformat(),
        "files": [os.path.basename(f) for f in FILES_TO_BACKUP if os.path.exists(f)],
    }
    with open(os.path.join(dest_dir, "_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"✅ {label}: backed up {copied} files to {dest_dir}")
    return copied


def run_baseline():
    if os.path.exists(BASELINE_DIR):
        print(f"⚠️  Baseline already exists at {BASELINE_DIR} — skipping (delete manually to re-create)")
        return
    backup_files(BASELINE_DIR, "Post-Draft 2026 Baseline")


def run_daily():
    # Wipe previous backup and replace with current state
    if os.path.exists(DAILY_DIR):
        shutil.rmtree(DAILY_DIR)
    backup_files(DAILY_DIR, f"Daily Backup {datetime.now().strftime('%Y-%m-%d')}")


if __name__ == "__main__":
    if "--baseline" in sys.argv:
        run_baseline()
    elif "--daily" in sys.argv:
        run_daily()
    else:
        print("Usage: python3 backup_data.py --baseline | --daily")
        sys.exit(1)
