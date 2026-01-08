"""Player log writer for new FBP Hub / bot transactions.

This module centralizes writing to data/player_log.json so future scripts
(trades, prospect purchases, promotions, etc.) can record their actions
in a consistent schema that the website reads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
PLAYER_LOG_FILE = DATA_DIR / "player_log.json"


@dataclass
class PlayerLogEntry:
    id: str
    season: int
    source: str  # e.g. "bot_trade", "bot_prospect_purchase"
    admin: str   # for now usually a system label or empty; UI hides it
    timestamp: str  # ISO 8601 UTC string
    upid: str
    player_name: str
    team: str
    pos: str
    age: Optional[int]
    level: str
    team_rank: Optional[int]
    rank: Optional[int]
    eta: str
    player_type: str
    owner: str
    contract: str
    status: str
    years: str
    update_type: str
    event: str


def _load_log() -> List[dict]:
    if not PLAYER_LOG_FILE.exists():
        return []
    try:
        with PLAYER_LOG_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        # If file is corrupt, do not crash transaction flow
        return []
    return []


def _save_log(entries: List[dict]) -> None:
    PLAYER_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PLAYER_LOG_FILE.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def append_entry(
    *,
    season: int,
    source: str,
    upid: str = "",
    player_name: str = "",
    team: str = "",
    pos: str = "",
    age: Optional[int] = None,
    level: str = "",
    team_rank: Optional[int] = None,
    rank: Optional[int] = None,
    eta: str = "",
    player_type: str = "",
    owner: str = "",
    contract: str = "",
    status: str = "",
    years: str = "",
    update_type: str = "",
    event: str = "",
    admin: str = "",
) -> PlayerLogEntry:
    """Append a transaction to player_log.json and return the entry.

    All fields mirror the player_log / history schema used by the website.
    The `admin` field is stored but currently hidden in the UI.
    """

    now = datetime.utcnow()
    timestamp = now.replace(microsecond=0).isoformat() + "Z"

    # Build a reasonably unique, stable-ish ID
    parts: List[str] = [
        str(season),
        timestamp,
        f"UPID_{upid}" if upid else player_name or "PLAYER",
        update_type or "UPDATE",
        source,
    ]
    rec_id = "-".join(p.replace(" ", "_") for p in parts if p)

    entry = PlayerLogEntry(
        id=rec_id,
        season=season,
        source=source,
        admin=admin,
        timestamp=timestamp,
        upid=upid,
        player_name=player_name,
        team=team,
        pos=pos,
        age=age,
        level=level,
        team_rank=team_rank,
        rank=rank,
        eta=eta,
        player_type=player_type,
        owner=owner,
        contract=contract,
        status=status,
        years=years,
        update_type=update_type,
        event=event,
    )

    log = _load_log()
    log.append(asdict(entry))
    _save_log(log)

    return entry


__all__ = ["PlayerLogEntry", "append_entry"]
