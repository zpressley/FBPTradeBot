#!/usr/bin/env python3
"""Sync team/roster metadata from fbp-trade-bot into fbp-hub's live managers.json.

Background (see docs handoff, July 2026 audit):
  - fbp-trade-bot/config/managers.json is the source of truth for roster
    identity fields: full_name, yahoo_team_id, role, standing, name,
    discord_id, final_rank_2025.
  - fbp-hub/config/managers.json is the file every fbp-hub page actually
    fetches (`fetch('./config/managers.json')`). It also holds the
    hand/script-maintained WizBucks allotment block (`wizbucks.2026...`)
    and kap_rollover_2026, which get updated independently by
    fbp-hub/scripts/update_kap_rollover_from_pad_submissions.py.
  - The previous daily-update.yml step did `cp config/managers.json
    fbp-hub/data/managers.json` — copying to a path nothing in fbp-hub
    reads, while the file everything actually reads never got the
    yahoo_team_id/role/standing/full_name fields at all. That's why
    role-gated admin UI in draft-picks.js was silently broken.

This script does an ADDITIVE merge only: for each team, it fills in any
key that's missing on the fbp-hub side using the fbp-trade-bot source,
and never overwrites a key that already exists in fbp-hub's file. That
means wizbucks/kap_rollover_2026/anything else the hub maintains on its
own is left untouched, while identity fields that only fbp-trade-bot
knows about (yahoo_team_id, role, standing, full_name) get backfilled.

Usage:
    python3 scripts/sync_managers_to_hub.py --hub-path ../fbp-hub
    python3 scripts/sync_managers_to_hub.py --hub-path ../fbp-hub --dry-run
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hub-path",
        default="fbp-hub",
        help="Path to the fbp-hub checkout (default: fbp-hub, sibling dir)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without writing the file",
    )
    args = parser.parse_args()

    src_path = ROOT / "config" / "managers.json"
    hub_path = Path(args.hub_path)
    if not hub_path.is_absolute():
        hub_path = (ROOT / hub_path).resolve()
    dst_path = hub_path / "config" / "managers.json"

    if not src_path.exists():
        print(f"ERROR: source file not found: {src_path}", file=sys.stderr)
        return 1
    if not dst_path.exists():
        print(f"ERROR: destination file not found: {dst_path}", file=sys.stderr)
        return 1

    src = json.loads(src_path.read_text())
    dst = json.loads(dst_path.read_text())

    if "teams" not in src or "teams" not in dst:
        print("ERROR: both files must have a top-level 'teams' object.", file=sys.stderr)
        return 1

    changed = {}
    for team_key, src_team in src["teams"].items():
        dst_team = dst["teams"].get(team_key)
        if dst_team is None:
            # A brand new team fbp-hub doesn't know about yet — add it whole.
            dst["teams"][team_key] = dict(src_team)
            changed[team_key] = sorted(src_team.keys())
            continue

        added_fields = []
        for field, value in src_team.items():
            if field not in dst_team:
                dst_team[field] = value
                added_fields.append(field)
        if added_fields:
            changed[team_key] = added_fields

    if not changed:
        print("✅ fbp-hub/config/managers.json already has all roster metadata fields. Nothing to do.")
        return 0

    print("Fields to backfill (additive only, nothing overwritten):")
    for team_key, fields in changed.items():
        print(f"  {team_key}: {', '.join(fields)}")

    if args.dry_run:
        print("\n--dry-run set: not writing changes.")
        return 0

    dst_path.write_text(json.dumps(dst, indent=2, ensure_ascii=False) + "\n")
    print(f"\n✅ Wrote {dst_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
