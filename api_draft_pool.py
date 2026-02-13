"""
FBP Draft Pool API Endpoint
Add to health.py:
    from api_draft_pool import router as draft_pool_router
    app.include_router(draft_pool_router)

Returns all Farm prospects with live drafted status, prospect_tags badges,
and ownership info baked in.
"""

import json
import os
from fastapi import APIRouter, Header, HTTPException, Query, Depends
from typing import Optional

router = APIRouter(prefix="/api/draft", tags=["draft-pool"])

COMBINED_FILE = "data/combined_players.json"
DRAFT_STATE_FILE = "data/draft_state_prospect_2026.json"
FYPD_FILE = "data/fypd_2026_rankings.json"
PROSPECT_TAGS_FILE = "data/prospect_tags.json"
API_KEY = os.getenv("BOT_API_KEY", "")


def verify_key(x_api_key: Optional[str] = Header(None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


def load_json(path, default=None):
    if default is None:
        default = []
    if not os.path.exists(path):
        return default
    with open(path, "r") as f:
        return json.load(f)


@router.get("/prospect/pool")
async def get_prospect_pool(
    fypd_only: bool = Query(False),
    available_only: bool = Query(True),
    search: Optional[str] = Query(None),
    authorized: bool = Depends(verify_key),
):
    """Return all Farm prospects with live draft status and prospect_tags data."""
    players = load_json(COMBINED_FILE, [])
    draft_state = load_json(DRAFT_STATE_FILE, {})
    fypd_rankings = load_json(FYPD_FILE, [])

    # Load prospect tags for badges/FV/status enrichment
    tags_raw = load_json(PROSPECT_TAGS_FILE, [])
    if isinstance(tags_raw, dict):
        tags_list = tags_raw.get("players", [])
    else:
        tags_list = tags_raw

    tags_by_upid = {}
    for t in tags_list:
        upid = str(t.get("upid", ""))
        if upid:
            tags_by_upid[upid] = t

    # FYPD lookup
    fypd_by_upid = {}
    for entry in fypd_rankings:
        upid = str(entry.get("upid", ""))
        if upid:
            fypd_by_upid[upid] = entry

    # Drafted lookup
    # IMPORTANT: Use UPID as the primary key so accents / punctuation /
    # formatting differences in player names do not break "drafted" detection.
    picks_made = draft_state.get("picks_made", [])

    drafted_by_upid = {}
    drafted_by_name = {}
    for pick in picks_made:
        upid = str(pick.get("upid") or "").strip()
        info = {
            "team": pick.get("team", ""),
            "round": pick.get("round", 0),
            "pick": pick.get("pick", 0),
        }
        if upid:
            drafted_by_upid[upid] = info

        # Fallback for older state files that may not have UPID.
        name = (pick.get("player") or "").lower().strip()
        if name:
            drafted_by_name[name] = info

    pool = []
    for p in players:
        if p.get("player_type") != "Farm":
            continue

        name = p.get("name", "")
        name_lower = name.lower().strip()
        upid = str(p.get("upid", ""))

        # Prefer UPID drafted detection; fall back to name-based detection.
        draft_info = drafted_by_upid.get(upid) or drafted_by_name.get(name_lower)
        drafted_by = draft_info["team"] if draft_info else None

        owner = p.get("manager", "") or p.get("FBP_Team", "")
        contract = p.get("contract_type", "") or ""
        years = p.get("years_simple", "") or ""
        is_owned = bool(owner and owner.strip())

        # FYPD
        fypd_entry = fypd_by_upid.get(upid, {})
        is_fypd = p.get("fypd", False) or bool(fypd_entry)
        fypd_rank = fypd_entry.get("rank") or fypd_entry.get("fypd_rank")

        # Prospect tags enrichment
        tag_data = tags_by_upid.get(upid, {})
        badges = tag_data.get("badges", [])
        fv = tag_data.get("fv", {})
        tag_status = tag_data.get("status", [])

        # Filters
        if fypd_only and not is_fypd:
            continue
        if available_only and (drafted_by or is_owned):
            continue
        if search:
            sl = search.lower()
            if (sl not in name_lower
                    and sl not in (p.get("team") or "").lower()
                    and sl not in (p.get("position") or "").lower()):
                continue

        pool.append({
            "upid": upid,
            "name": name,
            "team": p.get("team", ""),
            "position": p.get("position", ""),
            "age": p.get("age"),
            "bats": p.get("bats", ""),
            "throws": p.get("throws", ""),
            "rank": p.get("rank"),
            "fypd": is_fypd,
            "fypd_rank": fypd_rank,
            "owner": owner if is_owned else None,
            "contract_type": contract,
            "years_simple": years,
            "drafted_by": drafted_by,
            "draft_round": draft_info["round"] if draft_info else None,
            "draft_pick": draft_info["pick"] if draft_info else None,
            # Prospect tags data
            "badges": badges,
            "fv": fv,
            "tag_status": tag_status,
        })

    # Sort: FYPD ranked first, then by rank, then alphabetical
    def sort_key(p):
        fypd_r = p.get("fypd_rank") or 9999
        rank = p.get("rank") or 9999
        return (0 if p["fypd"] else 1, fypd_r, rank, p["name"])

    pool.sort(key=sort_key)

    return {
        "count": len(pool),
        "draft_status": draft_state.get("status", "not_started"),
        "current_pick_index": draft_state.get("current_pick_index", 0),
        "total_picks_made": len(picks_made),
        "players": pool,
    }
