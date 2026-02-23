from __future__ import annotations

import json
import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from team_utils import load_managers_config, normalize_team_abbr


router = APIRouter(prefix="/api/settings", tags=["settings"])

API_KEY = os.getenv("BOT_API_KEY", "")
TEAM_COLORS_PATH = "data/team_colors.json"

_commit_fn = None


def set_settings_commit_fn(fn) -> None:
    """Inject health.py's commit queue function (best-effort)."""

    global _commit_fn
    _commit_fn = fn


def verify_key(x_api_key: Optional[str] = Header(None)) -> bool:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


def require_manager_team(x_manager_team: Optional[str] = Header(None)) -> str:
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


_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _validate_hex_required(value: str, field: str) -> str:
    raw = str(value or "").strip()
    if not _HEX_COLOR_RE.match(raw):
        raise HTTPException(status_code=400, detail=f"Invalid {field} (expected #RRGGBB)")
    return raw.upper()


def _validate_hex_optional(value: Optional[str], field: str) -> Optional[str]:
    if value is None:
        return None

    raw = str(value).strip()
    if raw == "":
        return None

    if not _HEX_COLOR_RE.match(raw):
        raise HTTPException(status_code=400, detail=f"Invalid {field} (expected #RRGGBB or null)")

    return raw.upper()


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


class TeamColorsPayload(BaseModel):
    primary: str
    secondary: str
    accent1: Optional[str] = None
    accent2: Optional[str] = None
    accent3: Optional[str] = None


@router.post("/team-colors")
async def set_team_colors(
    payload: TeamColorsPayload,
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    colors = {
        "primary": _validate_hex_required(payload.primary, "primary"),
        "secondary": _validate_hex_required(payload.secondary, "secondary"),
        "accent1": _validate_hex_optional(payload.accent1, "accent1"),
        "accent2": _validate_hex_optional(payload.accent2, "accent2"),
        "accent3": _validate_hex_optional(payload.accent3, "accent3"),
    }

    # Load original data for rollback
    original_data = _load_json(TEAM_COLORS_PATH, {}) or {}
    if not isinstance(original_data, dict):
        original_data = {}
    
    # Make a copy and update it
    data = dict(original_data)
    data[manager_team] = colors
    
    # Save changes
    _save_json(TEAM_COLORS_PATH, data)
    print(f"💾 Saved team colors for {manager_team}")

    # CRITICAL: Commit to git (not optional!)
    try:
        if _commit_fn is None:
            # Rollback and fail
            _save_json(TEAM_COLORS_PATH, original_data)
            print("❌ Git commit system not initialized, rolled back")
            raise HTTPException(
                status_code=500,
                detail="Git commit system not initialized. Team colors NOT saved. Please contact admin."
            )
        
        _commit_fn([TEAM_COLORS_PATH], f"Team colors: {manager_team}")
        print(f"✅ Team colors committed to git for {manager_team}")
        
    except HTTPException:
        raise  # Re-raise formatted errors
    except Exception as exc:
        # Rollback on failure
        print(f"❌ Git commit failed: {exc}")
        print("🔄 Rolling back team colors...")
        _save_json(TEAM_COLORS_PATH, original_data)
        print("✅ Rollback complete")
        
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save team colors: {exc}. Changes NOT saved. Please try again."
        )

    return {"success": True, "team": manager_team, "colors": colors}
