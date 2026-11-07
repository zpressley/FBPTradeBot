"""Thin MLB Stats API client for Specs 03 and 07. Plain functions that return plain dicts.

Tests never call these: they pass the parsed rows straight to lineups.service.

Rate limits: see "External API Rate Limits" in WARP.md. Each call here makes at most 3 attempts
with 5 s then 30 s waits (BACKOFF_SECONDS[2] = 120 s is there if the attempt count is ever raised),
and 4xx answers other than 429 are not retried. The scheduled jobs that call these must also widen
their own retry interval after a failed run; never re-run them on a fixed short loop.

The response shapes below follow the public statsapi.mlb.com documentation but were written
without being able to call the API; check them against one live response before relying on them.
"""

from __future__ import annotations

import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

BASE = "https://statsapi.mlb.com/api/v1"
TIMEOUT_SECONDS = 30
ATTEMPTS = 3
BACKOFF_SECONDS = (5, 30, 120)
PAGE_SIZE = 500
ET = ZoneInfo("America/New_York")


def _get(path: str, params: dict) -> dict:
    last = None
    for attempt in range(ATTEMPTS):
        try:
            r = requests.get(BASE + path, params=params, timeout=TIMEOUT_SECONDS)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code} from {path}", response=r)
            r.raise_for_status()
            return r.json()
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
            last = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status is not None and status < 500 and status != 429:
                raise  # a real 4xx: retrying will not help
            if attempt < ATTEMPTS - 1:
                time.sleep(BACKOFF_SECONDS[attempt])
    raise last


_STATUS = {"Preview": "scheduled", "Live": "live", "Final": "final"}


def fetch_schedule(day: date) -> list[dict]:
    """The day's MLB games as rows for lineups.service.refresh_games."""
    data = _get("/schedule", {"sportId": 1, "date": day.isoformat(), "hydrate": "team,probablePitcher"})
    rows = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            status = g.get("status", {})
            state = "postponed" if status.get("detailedState") in ("Postponed", "Cancelled") \
                else _STATUS.get(status.get("abstractGameState"), "scheduled")
            home, away = g["teams"]["home"], g["teams"]["away"]
            rows.append({
                "game_pk": g["gamePk"],
                "game_date": date.fromisoformat(g.get("officialDate") or d["date"]),
                "start_time": datetime.fromisoformat(g["gameDate"].replace("Z", "+00:00")),
                "home_team": home["team"].get("abbreviation"),
                "away_team": away["team"].get("abbreviation"),
                "status": state,
                "probable_home_sp": (home.get("probablePitcher") or {}).get("id"),
                "probable_away_sp": (away.get("probablePitcher") or {}).get("id"),
            })
    return rows


def fetch_fielding(season: int) -> list[dict]:
    """Season fielding stats by position, for lineups.service.refresh_eligibility.

    Rows: {mlb_id, position, games, starts, relief}. Pitchers come back as position 'P'; relief is
    games minus starts.
    """
    rows, offset = [], 0
    while True:
        data = _get("/stats", {"stats": "season", "group": "fielding", "season": season, "sportId": 1,
                               "playerPool": "ALL", "limit": PAGE_SIZE, "offset": offset})
        splits = (data.get("stats") or [{}])[0].get("splits", [])
        for s in splits:
            st = s.get("stat", {})
            games, starts = st.get("games") or 0, st.get("gamesStarted") or 0
            rows.append({"mlb_id": s["player"]["id"], "position": (s.get("position") or {}).get("abbreviation"),
                         "games": games, "starts": starts, "relief": max(games - starts, 0)})
        if len(splits) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE
        time.sleep(1)  # be gentle between pages


def _status_from_code(code: str | None) -> str:
    if not code:
        return "other"
    if code == "A":
        return "active"
    if code.startswith("D"):      # D7, D10, D15, D60: injured lists
        return "il"
    if code in ("MIN", "NRI"):
        return "minors"
    return "other"


def fetch_player_positions(mlb_ids: list[int]) -> list[dict]:
    """Primary position and roster status for lineups.service.refresh_mlb_status.

    Rows: {mlb_id, primary_position, status ('active','il','minors','other'), status_code}.
    """
    rows = []
    ids = list(mlb_ids)
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        data = _get("/people", {"personIds": ",".join(str(x) for x in chunk), "hydrate": "rosterEntries"})
        for p in data.get("people", []):
            entries = [e for e in p.get("rosterEntries", []) if e.get("isActive", True)]
            entries.sort(key=lambda e: e.get("effectiveDate") or "")
            code = (entries[-1].get("statusCode") if entries else None)
            rows.append({"mlb_id": p["id"], "primary_position": (p.get("primaryPosition") or {}).get("abbreviation"),
                         "status": _status_from_code(code), "status_code": code})
        if i + 100 < len(ids):
            time.sleep(1)
    return rows


def innings_to_numeric(value) -> float | None:
    """MLB reports innings pitched as "12.1", meaning 12 and 1/3 -- the decimal is outs,
    not a fraction. Stored as a real number so prospect_limits.ip (100) compares correctly."""
    if value in (None, ""):
        return None
    try:
        whole, _, outs = str(value).partition(".")
        n = float(whole or 0)
        if outs:
            n += min(int(outs[0]), 2) / 3
        return round(n, 3)
    except (TypeError, ValueError):
        return None


def _career_stat(person: dict, group: str) -> dict:
    for s in person.get("stats", []):
        name = ((s.get("group") or {}).get("displayName") or "").lower()
        if name == group:
            splits = s.get("splits") or []
            if splits:
                return splits[0].get("stat") or {}
    return {}


def fetch_prospect_stats(mlb_ids: list[int]) -> list[dict]:
    """Career MLB totals and debut date, for league.actions.refresh_prospect_status (Spec 03).

    Rows: {mlb_id, mlb_debut (date or None), age, ab, pa, ip, appearances}.

    Career totals, not season: Spec 03's limits (350 PA, 100 IP, 30 appearances) are
    career MLB service, which is also what decides graduation. `appearances` is a
    pitcher's games pitched.
    """
    rows = []
    ids = list(mlb_ids)
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        data = _get("/people", {
            "personIds": ",".join(str(x) for x in chunk),
            "hydrate": "stats(group=[hitting,pitching],type=[career])",
        })
        for p in data.get("people", []):
            hitting, pitching = _career_stat(p, "hitting"), _career_stat(p, "pitching")
            debut = p.get("mlbDebutDate")
            rows.append({
                "mlb_id": p["id"],
                "mlb_debut": date.fromisoformat(debut) if debut else None,
                "age": p.get("currentAge"),
                "ab": hitting.get("atBats"),
                "pa": hitting.get("plateAppearances"),
                "ip": innings_to_numeric(pitching.get("inningsPitched")),
                "appearances": pitching.get("appearances") or pitching.get("gamesPitched"),
            })
        if i + 100 < len(ids):
            time.sleep(1)
    return rows
