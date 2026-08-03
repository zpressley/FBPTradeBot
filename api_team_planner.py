"""
Team Planner Save/Load API

Team Planner is an open roster-planning tool (no login wall, matches Team
Builder's existing looser access pattern): any caller may save or load the
plan for ANY team, gated only by the shared X-API-Key (same key the
Cloudflare Worker injects for the whole site). There is no per-manager
ownership check here by design — see docs/TRADE_PLANNER_PLAN.md §3.2.

Add to health.py:
    from api_team_planner import router as team_planner_router, set_team_planner_commit_fn
    app.include_router(team_planner_router)
    set_team_planner_commit_fn(_commit_and_push)

Endpoints:
- POST /api/team-planner/save: Upsert a team's plan for one mode ('kap' or 'pad')
- GET  /api/team-planner/{team}: Fetch a team's saved plan(s) (both modes, if saved)
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from team_utils import load_managers_config, normalize_team_abbr
from data_lock import DATA_LOCK

router = APIRouter(prefix="/api/team-planner", tags=["team-planner"])

API_KEY = os.getenv("BOT_API_KEY", "")
PLANS_PATH = "data/team_planner_plans.json"
VALID_MODES = ("kap", "pad")

_commit_fn = None


def set_team_planner_commit_fn(fn) -> None:
    """Inject health.py's centralised commit-queue function (best-effort)."""
    global _commit_fn
    _commit_fn = fn


def _verify_key(x_api_key: Optional[str] = Header(None)) -> bool:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


def _load_plans() -> dict:
    if not os.path.exists(PLANS_PATH):
        return {}
    try:
        with open(PLANS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_plans(data: dict) -> None:
    os.makedirs(os.path.dirname(PLANS_PATH), exist_ok=True)
    with open(PLANS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _resolve_team(team_token: str) -> str:
    cfg = load_managers_config()
    teams = cfg.get("teams") if isinstance(cfg.get("teams"), dict) else {}
    team = normalize_team_abbr(team_token, managers_data=cfg)
    if team not in teams:
        raise HTTPException(status_code=400, detail=f"Unknown team: {team_token}")
    return team


class TeamPlannerSavePayload(BaseModel):
    team: str
    mode: str
    plan: dict


@router.get("/{team}")
async def get_team_plan(team: str, _: bool = Depends(_verify_key)):
    """Return saved Team Planner state for a team: {"kap": {...}, "pad": {...}}
    (only whichever modes have actually been saved; {} if none yet)."""
    resolved = _resolve_team(team)
    with DATA_LOCK:
        data = _load_plans()
        return data.get(resolved, {})


@router.post("/save")
async def save_team_plan(payload: TeamPlannerSavePayload, _: bool = Depends(_verify_key)):
    """Upsert one mode of a team's plan. Last write wins per (team, mode)."""
    mode = (payload.mode or "").strip().lower()
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode '{payload.mode}'. Must be one of {VALID_MODES}.")

    resolved = _resolve_team(payload.team)
    saved_at = int(time.time() * 1000)  # ms epoch, matches Date.now() on the frontend

    with DATA_LOCK:
        data = _load_plans()
        team_entry = dict(data.get(resolved, {}))
        team_entry[mode] = {"plan": payload.plan, "savedAt": saved_at}
        data[resolved] = team_entry
        _save_plans(data)

    if _commit_fn:
        try:
            _commit_fn([PLANS_PATH], f"Team Planner save: {resolved} ({mode})")
        except Exception as exc:
            print(f"⚠️ Team Planner commit failed: {exc}")

    return {"success": True, "team": resolved, "mode": mode, "savedAt": saved_at}
