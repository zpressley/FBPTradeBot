from __future__ import annotations

import json
import os
import re
from typing import Optional


MANAGERS_CONFIG_PATH = "config/managers.json"


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_managers_config() -> dict:
    cfg = _load_json(MANAGERS_CONFIG_PATH, {}) or {}
    return cfg if isinstance(cfg, dict) else {}


def _slug(s: str) -> str:
    # Normalize to a comparison key: alnum only, lowercase.
    return re.sub(r"[^a-z0-9]+", "", (s or "").strip().lower())


def normalize_team_abbr(team_token: str, *, managers_data: Optional[dict] = None) -> str:
    """Normalize any team identifier to canonical FBP abbreviation.

    Accepts:
      - Abbreviation: "WIZ"
      - Franchise name: "Whiz Kids"
      - Display forms: "Whiz Kids (WIZ)", "WIZ - Whiz Kids", "Whiz Kids - WIZ"

    If it can't be resolved, returns the uppercased input (best-effort).
    """

    raw = str(team_token or "").strip()
    if not raw:
        return ""

    cfg = managers_data if isinstance(managers_data, dict) else load_managers_config()
    teams = cfg.get("teams") if isinstance(cfg.get("teams"), dict) else {}

    # 1) Direct abbreviation match
    upper = raw.upper()
    if upper in teams:
        return upper

    # 2) Extract trailing (ABBR) or [ABBR]
    m = re.search(r"\(([A-Za-z0-9_]{2,5})\)\s*$", raw)
    if m:
        cand = m.group(1).strip().upper()
        if cand in teams:
            return cand

    m = re.search(r"\[([A-Za-z0-9_]{2,5})\]\s*$", raw)
    if m:
        cand = m.group(1).strip().upper()
        if cand in teams:
            return cand

    # 3) Handle simple dash forms like "WIZ - Whiz Kids" or "Whiz Kids - WIZ"
    parts = [p.strip() for p in re.split(r"\s*[\-\u2013\u2014]\s*", raw) if p.strip()]
    if len(parts) >= 2:
        for cand_raw in (parts[0], parts[-1]):
            cand = cand_raw.strip().upper()
            if cand in teams:
                return cand

    # 4) Match by franchise name (punctuation/whitespace-insensitive)
    target = _slug(raw)
    if target:
        for abbr, meta in teams.items():
            if not isinstance(meta, dict):
                continue
            name = str(meta.get("name") or "").strip()
            if name and _slug(name) == target:
                return str(abbr).upper()

    return upper
