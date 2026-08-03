"""
Shared MLB Stats API lookup helpers for the "add player" flows.

Used by both api_admin_bulk.py's POST /api/admin/enrich-player and
api_manager_players.py's manager add-player-request enrichment step.
Previously each file had its own near-identical name-search
implementation; this module is the single copy, plus the new ID-based
lookup both flows are being upgraded to prefer.

fetch_player_by_mlb_id() is why this module exists: an exact
GET /people/{id} call is unambiguous, unlike a name search, which can
silently match the wrong same-named player or miss on an accent/suffix
mismatch (the exact failure mode behind several real duplicate-player
incidents this system has hit -- see e.g.
scripts/fix_garcia_jr_canonical_upid_2026_08_02.py). Prefer this whenever
an mlb_id is available.

fetch_player_by_name() is the pre-existing fallback for when no ID is
known yet -- unchanged behavior, just no longer duplicated field-for-field
in two files.

No Yahoo Fantasy API lookup here. There's no proven single-ID bio
endpoint for Yahoo in this codebase -- only stats-by-ID (XML, see
calculate_baselines.py) and a roster-only bulk player pull (see
fetch_yahoo_all_players.py) -- and building one would require invoking
the OAuth token flow (token_manager.get_access_token(), which can refresh
and rewrite token.json) just to find out whether bio-field hydration even
works. So a yahoo_id is accepted and stored as a plain identifier, not
auto-enriched. Worth revisiting if that's needed later.
"""

from __future__ import annotations

from typing import Any, Optional

import requests

MLB_STATS_BASE = "https://statsapi.mlb.com/api/v1"


def _shape_person(person: dict) -> dict:
    """Map an MLB Stats API `people[]` entry to this app's field names."""
    enriched: dict[str, Any] = {
        "mlb_id": str(person.get("id", "")) or None,
        "name": person.get("fullName"),
        "birth_date": person.get("birthDate"),
        "debut_date": person.get("mlbDebutDate"),
        "bats": (person.get("batSide") or {}).get("code"),
        "throws": (person.get("pitchHand") or {}).get("code"),
        "position": (person.get("primaryPosition") or {}).get("abbreviation"),
        "mlb_primary_position": (person.get("primaryPosition") or {}).get("abbreviation"),
        "team": (person.get("currentTeam") or {}).get("abbreviation"),
        "age": person.get("currentAge"),
    }
    return {k: v for k, v in enriched.items() if v not in (None, "")}


def fetch_player_by_mlb_id(mlb_id: Any) -> Optional[dict]:
    """Exact lookup by MLB Stats API person ID. Returns None if the ID
    doesn't resolve to a real person or the request fails -- callers
    should treat that as "couldn't verify," not necessarily "invalid,"
    since it could also be a transient API issue."""
    mlb_id_str = str(mlb_id or "").strip()
    if not mlb_id_str or not mlb_id_str.isdigit():
        return None

    try:
        resp = requests.get(
            f"{MLB_STATS_BASE}/people/{mlb_id_str}",
            params={"hydrate": "currentTeam"},
            timeout=10,
        )
    except Exception as exc:
        print(f"⚠️ MLB API request failed for mlb_id={mlb_id_str}: {exc}")
        return None

    if resp.status_code != 200:
        return None

    try:
        people = (resp.json() or {}).get("people") or []
    except Exception as exc:
        print(f"⚠️ MLB API returned unparseable response for mlb_id={mlb_id_str}: {exc}")
        return None

    if not isinstance(people, list) or not people:
        return None

    return _shape_person(people[0])


def fetch_player_by_name(name: str, team_hint: Optional[str] = None) -> dict:
    """Best-effort name search (existing behavior, moved here unchanged).
    Always returns a dict (possibly empty) rather than None, matching the
    two callers' prior behavior of treating "no match" as "nothing to
    enrich" rather than an error."""
    name = (name or "").strip()
    if not name:
        return {}

    try:
        resp = requests.get(
            f"{MLB_STATS_BASE}/people/search",
            params={"names": name, "hydrate": "currentTeam"},
            timeout=10,
        )
    except Exception as exc:
        print(f"⚠️ MLB API request failed while enriching player '{name}': {exc}")
        return {}

    if resp.status_code != 200:
        return {}

    try:
        people = (resp.json() or {}).get("people") or []
    except Exception as exc:
        print(f"⚠️ MLB API returned unparseable response for name='{name}': {exc}")
        return {}

    if not isinstance(people, list) or not people:
        return {}

    best = None
    for person in people:
        if not isinstance(person, dict):
            continue
        if team_hint:
            current_team = ((person.get("currentTeam") or {}).get("abbreviation") or "").upper()
            if current_team == team_hint.upper():
                best = person
                break
        if best is None:
            best = person

    if not isinstance(best, dict):
        return {}

    return _shape_person(best)


def enrich_player_data(
    name: Optional[str] = None,
    team_hint: Optional[str] = None,
    mlb_id: Optional[Any] = None,
) -> dict:
    """Single entry point for both add-player flows: prefer an exact
    mlb_id lookup when one is given, otherwise fall back to a name
    search. Returns {} if nothing resolves either way (e.g. a bad ID and
    no usable name) -- callers already treat an empty dict as "no
    enrichment available" rather than an error."""
    if mlb_id:
        by_id = fetch_player_by_mlb_id(mlb_id)
        if by_id:
            return by_id
        print(f"⚠️ mlb_id={mlb_id!r} did not resolve via MLB Stats API; falling back to name search" if name else f"⚠️ mlb_id={mlb_id!r} did not resolve via MLB Stats API")

    if name:
        return fetch_player_by_name(name, team_hint)

    return {}
