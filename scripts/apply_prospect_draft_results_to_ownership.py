"""Apply 2026 prospect draft results to combined_players ownership + player_log.

Reads:
- data/draft_order_2026.json (expects each pick has a result with UPID)

Writes:
- data/combined_players.json (updates manager/FBP_Team/contract_type)
- data/player_log.json (appends snapshot entries)

Backups:
- data/Backups/combined_players.json.bak_<timestamp>

Log entries:
- update_type: "Draft"
- event: "26 Prospect Draft"
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from typing import Any

# Allow importing repo modules when running from scripts/.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from draft.draft_manager import _resolve_team_name
from pad.pad_processor import _append_player_log_entry, _load_json, _save_json


ORDER_PATH = "data/draft_order_2026.json"
COMBINED_PATH = "data/combined_players.json"
PLAYER_LOG_PATH = "data/player_log.json"

SEASON = 2026
LOG_SOURCE = "26_PROSPECT_DRAFT"
LOG_UPDATE_TYPE = "Draft"
LOG_EVENT = "26 Prospect Draft"
LOG_ADMIN = "draft_order_sync"


def _load_order_picks(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data
    picks = data.get("picks") or data.get("rounds") or []
    if not isinstance(picks, list):
        return []
    return picks


def _backup_file(path: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = "data/Backups"
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(backup_dir, os.path.basename(path) + f".bak_{ts}")
    shutil.copy2(path, backup_path)
    return backup_path


def main() -> None:
    if not os.path.exists(ORDER_PATH):
        raise SystemExit(f"Missing {ORDER_PATH}")
    if not os.path.exists(COMBINED_PATH):
        raise SystemExit(f"Missing {COMBINED_PATH}")

    picks = _load_order_picks(ORDER_PATH)
    picks_with_results = [p for p in picks if p.get("result")]

    combined_players = _load_json(COMBINED_PATH) or []
    if not isinstance(combined_players, list):
        raise SystemExit(f"{COMBINED_PATH} is not a list")

    by_upid: dict[str, dict[str, Any]] = {}
    for rec in combined_players:
        upid = str(rec.get("upid") or "").strip()
        if upid:
            by_upid[upid] = rec

    missing_upids: list[str] = []
    updated = 0

    # Apply ownership updates in-memory
    for pick in picks_with_results:
        result = pick.get("result") or {}
        upid = str(result.get("upid") or "").strip()
        if not upid:
            continue

        player_rec = by_upid.get(upid)
        if not player_rec:
            missing_upids.append(upid)
            continue

        team = str(pick.get("team") or "").strip().upper()
        if not team:
            continue

        franchise_name = _resolve_team_name(team)

        round_type = str(pick.get("round_type") or "").strip().lower()
        rnd = int(pick.get("round") or 0)

        # Contract mapping:
        # - FYPD rounds -> Blue Chip Contract
        # - DC rounds   -> Development Cont.
        if round_type == "fypd":
            contract_type = "Blue Chip Contract"
        elif round_type in ("dc", "development", "prospect"):
            contract_type = "Development Cont."
        else:
            # Fallback to round-number convention (Rounds 1-2 are FYPD)
            contract_type = "Blue Chip Contract" if rnd <= 2 else "Development Cont."

        # Update ownership fields. (Roster pages key off FBP_Team.)
        player_rec["manager"] = franchise_name
        player_rec["FBP_Team"] = team
        player_rec["contract_type"] = contract_type

        updated += 1

    if missing_upids:
        raise SystemExit(
            "Draft results contain UPIDs missing from combined_players.json: "
            + ", ".join(sorted(set(missing_upids))[:20])
            + (" ..." if len(set(missing_upids)) > 20 else "")
        )

    # Backup + persist combined_players
    backup_path = _backup_file(COMBINED_PATH)
    _save_json(COMBINED_PATH, combined_players)

    # Append player_log entries (idempotent per UPID + event)
    player_log = _load_json(PLAYER_LOG_PATH) or []
    if not isinstance(player_log, list):
        player_log = []

    existing = set()
    for e in player_log:
        try:
            if e.get("season") != SEASON:
                continue
            if (e.get("update_type") or "") != LOG_UPDATE_TYPE:
                continue
            if (e.get("event") or "") != LOG_EVENT:
                continue
            upid = str(e.get("upid") or "").strip()
            if upid:
                existing.add(upid)
        except Exception:
            continue

    appended = 0
    for pick in picks_with_results:
        result = pick.get("result") or {}
        upid = str(result.get("upid") or "").strip()
        if not upid or upid in existing:
            continue

        player_rec = by_upid.get(upid)
        if not player_rec:
            continue

        _append_player_log_entry(
            player_log,
            player_rec,
            season=SEASON,
            source=LOG_SOURCE,
            update_type=LOG_UPDATE_TYPE,
            event=LOG_EVENT,
            admin=LOG_ADMIN,
        )
        appended += 1

    _save_json(PLAYER_LOG_PATH, player_log)

    print(
        json.dumps(
            {
                "ok": True,
                "picks_with_results": len(picks_with_results),
                "combined_players_updated": updated,
                "combined_players_backup": backup_path,
                "player_log_entries_appended": appended,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
