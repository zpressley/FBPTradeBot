"""
FBP Hub - UPID Database API Endpoints

Handles:
  - GET  /api/upid/search?q=<query>&dupes_only=false
  - PUT  /api/upid/{upid}/alt-names
  - PUT  /api/upid/{upid}/approved-dupes
  - PUT  /api/upid/{upid}
"""

import json
import os
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request, Depends, Header

from data_lock import DATA_LOCK

router = APIRouter(prefix="/api/upid", tags=["upid"])

UPID_DB_FILE = "data/upid_database.json"
API_KEY = os.getenv("BOT_API_KEY", "")

_commit_fn: Optional[Callable[[list[str], str], None]] = None


def set_upid_commit_fn(fn: Callable[[list[str], str], None]) -> None:
    """Inject the centralised commit-and-push function from health.py."""
    global _commit_fn
    _commit_fn = fn


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_upid_db() -> dict:
    if not os.path.exists(UPID_DB_FILE):
        return {"by_upid": {}, "name_index": {}}
    with open(UPID_DB_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "by_upid" not in data:
        return {"by_upid": {}, "name_index": {}}
    return data


def _save_upid_db(data: dict) -> None:
    os.makedirs(os.path.dirname(UPID_DB_FILE), exist_ok=True)
    with open(UPID_DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _enqueue_commit(message: str) -> None:
    if _commit_fn is not None:
        _commit_fn([UPID_DB_FILE], message)
    else:
        print(f"⚠️ No commit function configured – skipping: {message}")


def _rebuild_name_index(upid_db: dict) -> None:
    """Rebuild the entire name_index from by_upid records."""
    name_index: dict[str, list[str]] = {}
    for upid, rec in upid_db.get("by_upid", {}).items():
        names = [rec.get("name", "")] + (rec.get("alt_names") or [])
        for n in names:
            key = (n or "").strip().lower()
            if key:
                name_index.setdefault(key, []).append(str(upid))
    upid_db["name_index"] = name_index


# ---------------------------------------------------------------------------
# GET /api/upid/search
# ---------------------------------------------------------------------------
@router.get("/search")
async def search_upid(
    q: str = "",
    dupes_only: bool = False,
    _=Depends(verify_api_key),
):
    """Search UPID database by name (substring) or exact UPID.

    Returns up to 50 matching records.
    """
    query = (q or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")

    with DATA_LOCK:
        upid_db = _load_upid_db()

    by_upid = upid_db.get("by_upid", {})
    results = []

    # Exact UPID match
    if query.isdigit() and query in by_upid:
        rec = by_upid[query]
        if not dupes_only or (rec.get("approved_dupes") or "").upper() != "FALSE":
            results.append(rec)
    else:
        # Substring search on name + alt_names
        q_lower = query.lower()
        for upid, rec in by_upid.items():
            if dupes_only and (rec.get("approved_dupes") or "").upper() == "FALSE":
                continue

            name = (rec.get("name") or "").lower()
            alt_names = [n.lower() for n in (rec.get("alt_names") or [])]
            all_names = [name] + alt_names

            if any(q_lower in n for n in all_names):
                results.append(rec)
                if len(results) >= 50:
                    break

    return {"results": results, "count": len(results)}


# ---------------------------------------------------------------------------
# PUT /api/upid/{upid}/alt-names
# ---------------------------------------------------------------------------
@router.put("/{upid}/alt-names")
async def update_alt_names(
    upid: str,
    request: Request,
    _=Depends(verify_api_key),
):
    """Update alternate names for a UPID record.

    Rebuilds name_index entries for the affected record.
    """
    body = await request.json()
    alt_names = body.get("alt_names", [])
    admin = body.get("admin", "unknown")

    if not isinstance(alt_names, list):
        raise HTTPException(status_code=400, detail="alt_names must be a list")

    with DATA_LOCK:
        upid_db = _load_upid_db()
        rec = upid_db["by_upid"].get(upid)
        if not rec:
            raise HTTPException(status_code=404, detail=f"UPID {upid} not found")

        old_alt = rec.get("alt_names", [])
        rec["alt_names"] = alt_names
        _rebuild_name_index(upid_db)
        _save_upid_db(upid_db)

    _enqueue_commit(f"UPID {upid}: alt-names updated by {admin}")

    return {
        "upid": upid,
        "alt_names": alt_names,
        "previous_alt_names": old_alt,
    }


# ---------------------------------------------------------------------------
# PUT /api/upid/{upid}/approved-dupes
# ---------------------------------------------------------------------------
@router.put("/{upid}/approved-dupes")
async def update_approved_dupes(
    upid: str,
    request: Request,
    _=Depends(verify_api_key),
):
    """Toggle the approved_dupes flag for a UPID record."""
    body = await request.json()
    new_value = body.get("approved_dupes", "FALSE")
    admin = body.get("admin", "unknown")

    if new_value not in ("TRUE", "FALSE"):
        raise HTTPException(status_code=400, detail="approved_dupes must be 'TRUE' or 'FALSE'")

    with DATA_LOCK:
        upid_db = _load_upid_db()
        rec = upid_db["by_upid"].get(upid)
        if not rec:
            raise HTTPException(status_code=404, detail=f"UPID {upid} not found")

        old_value = rec.get("approved_dupes", "FALSE")
        rec["approved_dupes"] = new_value
        _save_upid_db(upid_db)

    _enqueue_commit(f"UPID {upid}: approved_dupes → {new_value} by {admin}")

    return {
        "upid": upid,
        "approved_dupes": new_value,
        "previous": old_value,
    }


# ---------------------------------------------------------------------------
# PUT /api/upid/{upid}
# ---------------------------------------------------------------------------
@router.put("/{upid}")
async def update_upid_record(
    upid: str,
    request: Request,
    _=Depends(verify_api_key),
):
    """General UPID record update (name, team, pos).

    Rebuilds name_index if the name changes.
    """
    body = await request.json()
    changes = body.get("changes", {})
    admin = body.get("admin", "unknown")

    if not changes:
        raise HTTPException(status_code=400, detail="No changes provided")

    allowed_fields = {"name", "team", "pos"}
    invalid = set(changes.keys()) - allowed_fields
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid fields: {invalid}")

    with DATA_LOCK:
        upid_db = _load_upid_db()
        rec = upid_db["by_upid"].get(upid)
        if not rec:
            raise HTTPException(status_code=404, detail=f"UPID {upid} not found")

        applied = {}
        for field, value in changes.items():
            old = rec.get(field)
            rec[field] = value
            applied[field] = {"from": old, "to": value}

        # Rebuild name index if name changed
        if "name" in changes:
            _rebuild_name_index(upid_db)

        _save_upid_db(upid_db)

    _enqueue_commit(f"UPID {upid}: updated {list(changes.keys())} by {admin}")

    return {
        "upid": upid,
        "changes": applied,
        "record": rec,
    }
