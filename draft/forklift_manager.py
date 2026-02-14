"""Forklift Mode Manager

Forklift Mode = admin-controlled auto-draft mode for managers who can't attend.

Features:
- 10-second timer instead of the normal 4 minutes
- Auto-picks from a team's personal draft board
- Persists across bot restarts via data/forklift_mode_{draft_type}_{season}.json

This module is intentionally lightweight and does not depend on Discord.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class ForkliftConfig:
    forklift_timer_seconds: int = 10
    normal_timer_seconds: int = 240


class ForkliftManager:
    """Stateful manager for which teams are in forklift mode."""

    def __init__(self, season: int = 2026, draft_type: str = "prospect"):
        self.season = int(season)
        self.draft_type = str(draft_type)
        self.state_file = f"data/forklift_mode_{self.draft_type}_{self.season}.json"

        self.config = ForkliftConfig()
        self.state = self.load_state()

    def load_state(self) -> Dict:
        """Load forklift mode state from disk (or initialize defaults)."""
        if not os.path.exists(self.state_file):
            return {
                "forklift_teams": [],  # list[str]
                "history": [],  # list[dict]
            }

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

        if not isinstance(data, dict):
            data = {}

        data.setdefault("forklift_teams", [])
        data.setdefault("history", [])
        return data

    def save_state(self) -> None:
        os.makedirs("data", exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def enable_forklift(self, team: str, enabled_by: str = "admin") -> Tuple[bool, str]:
        team = (team or "").upper().strip()
        if not team:
            return False, "Team is required"

        teams = self.state.setdefault("forklift_teams", [])
        if team in teams:
            return False, f"{team} is already in Forklift Mode"

        teams.append(team)
        self.state.setdefault("history", []).append(
            {
                "team": team,
                "action": "enabled",
                "by": enabled_by,
                "timestamp": datetime.now().isoformat(),
            }
        )
        self.save_state()

        return True, (
            f":forklift: **FORKLIFT MODE ENABLED** for {team}\n"
            f"└─ Timer: {self.config.forklift_timer_seconds}s (instead of {self.config.normal_timer_seconds}s)\n"
            f"└─ Auto-pick: enabled (from draft board)"
        )

    def disable_forklift(self, team: str, disabled_by: str = "admin") -> Tuple[bool, str]:
        team = (team or "").upper().strip()
        if not team:
            return False, "Team is required"

        teams = self.state.setdefault("forklift_teams", [])
        if team not in teams:
            return False, f"{team} is not in :forklift: Forklift Mode"

        teams.remove(team)
        self.state.setdefault("history", []).append(
            {
                "team": team,
                "action": "disabled",
                "by": disabled_by,
                "timestamp": datetime.now().isoformat(),
            }
        )
        self.save_state()

        return True, (
            f"✅ Forklift Mode disabled for {team}\n"
            f"└─ Timer: {self.config.normal_timer_seconds}s (normal)"
        )

    def is_forklift_enabled(self, team: str) -> bool:
        team = (team or "").upper().strip()
        return team in (self.state.get("forklift_teams") or [])

    def get_timer_duration(self, team: str) -> int:
        return (
            self.config.forklift_timer_seconds
            if self.is_forklift_enabled(team)
            else self.config.normal_timer_seconds
        )

    def get_forklift_teams(self) -> List[str]:
        teams = self.state.get("forklift_teams") or []
        return list(teams)

    def get_recent_changes(self, limit: int = 5) -> List[Dict]:
        hist = self.state.get("history") or []
        if not isinstance(hist, list):
            return []
        return hist[-limit:]
