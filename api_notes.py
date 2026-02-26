from __future__ import annotations

import json
import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from team_utils import load_managers_config, normalize_team_abbr


router = APIRouter(prefix="/api/notes", tags=["notes"])

API_KEY = os.getenv("BOT_API_KEY", "")
NOTES_PATH = "data/manager_notes.json"
NOTES_MAX = 500

_commit_fn = None


def set_notes_commit_fn(fn) -> None:
    """Inject health.py's commit queue function (best-effort)."""
    global _commit_fn
    _commit_fn = fn


def _verify_key(x_api_key: Optional[str] = Header(None)) -> bool:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


def _require_manager_team(x_manager_team: Optional[str] = Header(None)) -> str:
    if not x_manager_team:
        raise HTTPException(status_code=401, detail="Missing X-Manager-Team")

    team = normalize_team_abbr(str(x_manager_team).strip())
    if not team:
        raise HTTPException(status_code=401, detail="Missing X-Manager-Team")

    cfg = load_managers_config()
    teams = cfg.get("teams") if isinstance(cfg.get("teams"), dict) else {}
    if team not in teams:
        raise HTTPException(status_code=400, detail=f"Unknown team: {team}")

    return team


def _load_notes() -> dict:
    if not os.path.exists(NOTES_PATH):
        return {}
    with open(NOTES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _save_notes(data: dict) -> None:
    os.makedirs(os.path.dirname(NOTES_PATH), exist_ok=True)
    with open(NOTES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


class NotesPayload(BaseModel):
    notes: str


@router.get("")
async def get_notes(
    _: bool = Depends(_verify_key),
    manager_team: str = Depends(_require_manager_team),
):
    data = _load_notes()
    return {"team": manager_team, "notes": data.get(manager_team, "")}


@router.post("")
async def save_notes(
    payload: NotesPayload,
    _: bool = Depends(_verify_key),
    manager_team: str = Depends(_require_manager_team),
):
    text = payload.notes[:NOTES_MAX]

    original_data = _load_notes()
    data = dict(original_data)
    data[manager_team] = text
    _save_notes(data)

    if _commit_fn:
        try:
            _commit_fn([NOTES_PATH], f"Manager notes: {manager_team}")
        except Exception as exc:
            print(f"⚠️ Notes commit failed: {exc}")

    return {"success": True, "team": manager_team, "notes": text}
