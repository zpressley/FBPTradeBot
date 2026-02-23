"""
FBP Hub - Admin API Endpoints
Add these routes to your FastAPI app in health.py

Handles:
  - POST /api/admin/bulk-graduate
  - POST /api/admin/bulk-update-contracts
  - POST /api/admin/bulk-release
  - POST /api/admin/add-player
  - POST /api/admin/enrich-player
"""

import json
import os
import requests
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request, Depends, Header
from typing import Callable, Optional

router = APIRouter(prefix="/api/admin", tags=["admin-bulk"])

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
COMBINED_FILE = "data/combined_players.json"
UPID_DB_FILE = "data/upid_database.json"
PLAYER_LOG_FILE = "data/player_log.json"
API_KEY = os.getenv("BOT_API_KEY", "")
ADMIN_LOG_CHANNEL_ID = 1079466810375688262  # channel for admin change notifications

# Bot reference for Discord notifications
_bot_ref = None
_commit_fn: Optional[Callable[[list[str], str], None]] = None


def set_bulk_bot_reference(bot):
    """Called from health.py after bot starts to enable Discord notifications."""
    global _bot_ref
    _bot_ref = bot


def set_bulk_commit_fn(fn: Callable[[list[str], str], None]) -> None:
    """Inject the centralised commit-and-push function from health.py."""
    global _commit_fn
    _commit_fn = fn


async def _send_admin_bulk_notification(message: str):
    """Send a notification to the admin log Discord channel."""
    global _bot_ref
    if _bot_ref is None:
        return
    try:
        channel = _bot_ref.get_channel(ADMIN_LOG_CHANNEL_ID)
        if channel:
            await channel.send(message)
    except Exception as exc:
        print(f"⚠️ Failed to send bulk admin notification: {exc}")

# Repos (for git commit + push to website)
WEBSITE_REPO = os.getenv("WEBSITE_REPO", "")       # e.g. "username/fbp-hub"
WEBSITE_TOKEN = os.getenv("WEBSITE_REPO_TOKEN", "") # GitHub PAT

# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------
async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
def load_json(path):
    if not os.path.exists(path):
        return [] if path.endswith("player_log.json") else {}
    with open(path, "r") as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def log_player_action(upid, player_name, update_type, event, admin, owner="", changes=None):
    """Append an entry to player_log.json"""
    logs = load_json(PLAYER_LOG_FILE)
    if not isinstance(logs, list):
        logs = []

    entry = {
        "log_id": f"player_{int(datetime.now(timezone.utc).timestamp())}_{upid}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "season": datetime.now().year,
        "source": "admin_portal",
        "admin": admin,
        "upid": str(upid),
        "player_name": player_name,
        "owner": owner,
        "update_type": update_type,
        "event": event,
        "changes": changes or {},
    }
    logs.append(entry)
    save_json(PLAYER_LOG_FILE, logs)
    return entry


def _enqueue_commit(files: list[str], message: str) -> None:
    """Queue a git commit via the centralised commit worker from health.py.

    Falls back to a warning if no commit function has been injected yet.
    """
    if _commit_fn is not None:
        _commit_fn(files, message)
    else:
        print(f"⚠️ No commit function configured – skipping git commit: {message}")


def sync_to_website(files, message):
    """
    Push updated data files to the website repo via GitHub API.
    Only runs if WEBSITE_REPO and WEBSITE_TOKEN are configured.
    """
    if not WEBSITE_REPO or not WEBSITE_TOKEN:
        return

    headers = {
        "Authorization": f"token {WEBSITE_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    for filepath in files:
        if not os.path.exists(filepath):
            continue

        with open(filepath, "r") as f:
            content = f.read()

        import base64
        encoded = base64.b64encode(content.encode()).decode()

        api_url = f"https://api.github.com/repos/{WEBSITE_REPO}/contents/{filepath}"

        # Get current SHA (needed for update)
        sha = None
        resp = requests.get(api_url, headers=headers)
        if resp.status_code == 200:
            sha = resp.json().get("sha")

        payload = {
            "message": message,
            "content": encoded,
        }
        if sha:
            payload["sha"] = sha

        put_resp = requests.put(api_url, headers=headers, json=payload)
        if put_resp.status_code in (200, 201):
            print(f"✅ Synced {filepath} to website repo")
        else:
            print(f"⚠️ Failed to sync {filepath}: {put_resp.status_code} {put_resp.text[:200]}")


# ---------------------------------------------------------------------------
# POST /api/admin/bulk-graduate
# ---------------------------------------------------------------------------
@router.post("/bulk-graduate")
async def bulk_graduate(request: Request, _=Depends(verify_api_key)):
    body = await request.json()
    admin = body.get("admin", "unknown")
    upids = body.get("upids", [])
    default_tier = body.get("contract_tier", "TC R")
    upid_tier_map = body.get("upid_tier_map", {})  # Per-player overrides (BC → TC BC-1)

    if not upids:
        raise HTTPException(status_code=400, detail="No UPIDs provided")

    players = load_json(COMBINED_FILE)
    updated = []

    for p in players:
        p_upid = str(p.get("upid"))
        if p_upid in [str(u) for u in upids]:
            # Use per-player tier if provided, else default
            contract_tier = upid_tier_map.get(p_upid, default_tier)

            old_type = p.get("player_type", "")
            old_contract = p.get("years_simple", "")
            old_contract_type = p.get("contract_type", "")

            p["player_type"] = "MLB"
            p["years_simple"] = contract_tier
            p["contract_type"] = "Keeper Contract"

            log_player_action(
                upid=p.get("upid"),
                player_name=p.get("name", "Unknown"),
                update_type="Graduate",
                event=f"Graduated to {contract_tier} by {admin}",
                admin=admin,
                owner=p.get("manager", ""),
                changes={
                    "player_type": {"from": old_type, "to": "MLB"},
                    "years_simple": {"from": old_contract, "to": contract_tier},
                    "contract_type": {"from": old_contract_type, "to": "Keeper Contract"},
                },
            )
            updated.append({"name": p.get("name", "Unknown"), "tier": contract_tier})

    save_json(COMBINED_FILE, players)

    # Commit + sync
    commit_msg = f"Admin bulk graduate: {len(updated)} players"
    _enqueue_commit([COMBINED_FILE, PLAYER_LOG_FILE], commit_msg)
    sync_to_website([COMBINED_FILE, PLAYER_LOG_FILE], commit_msg)

    # Discord notification
    if _bot_ref and updated:
        player_names = ', '.join([p['name'] for p in updated[:5]])
        if len(updated) > 5:
            player_names += f' (+{len(updated) - 5} more)'
        discord_msg = f"🎓 **Bulk Graduate**\n\n👥 {len(updated)} players graduated\n📝 Players: {player_names}\n👤 Admin: {admin}\n💾 Source: Website Admin Portal"
        _bot_ref.loop.create_task(_send_admin_bulk_notification(discord_msg))

    return {"count": len(updated), "players": updated}


# ---------------------------------------------------------------------------
# POST /api/admin/bulk-update-contracts
# ---------------------------------------------------------------------------
@router.post("/bulk-update-contracts")
async def bulk_update_contracts(request: Request, _=Depends(verify_api_key)):
    body = await request.json()
    admin = body.get("admin", "unknown")
    upids = body.get("upids", [])
    new_contract = body.get("new_contract", "")

    if not upids:
        raise HTTPException(status_code=400, detail="No UPIDs provided")

    players = load_json(COMBINED_FILE)
    updated = []

    for p in players:
        if str(p.get("upid")) in [str(u) for u in upids]:
            old_contract = p.get("years_simple", "")

            if old_contract != new_contract:
                p["years_simple"] = new_contract

                log_player_action(
                    upid=p.get("upid"),
                    player_name=p.get("name", "Unknown"),
                    update_type="Admin",
                    event=f"Bulk contract update: {old_contract or '(none)'} → {new_contract or '(none)'} by {admin}",
                    admin=admin,
                    owner=p.get("manager", ""),
                    changes={"years_simple": {"from": old_contract, "to": new_contract}},
                )
                updated.append(p.get("name", "Unknown"))

    save_json(COMBINED_FILE, players)

    commit_msg = f"Admin bulk contract update: {len(updated)} players → {new_contract or '(none)'}"
    _enqueue_commit([COMBINED_FILE, PLAYER_LOG_FILE], commit_msg)
    sync_to_website([COMBINED_FILE, PLAYER_LOG_FILE], commit_msg)

    # Discord notification
    if _bot_ref and updated:
        player_names = ', '.join(updated[:5])
        if len(updated) > 5:
            player_names += f' (+{len(updated) - 5} more)'
        discord_msg = f"📝 **Bulk Contract Update**\n\n👥 {len(updated)} players updated\n📄 New Contract: {new_contract or '(none)'}\n📝 Players: {player_names}\n👤 Admin: {admin}\n💾 Source: Website Admin Portal"
        _bot_ref.loop.create_task(_send_admin_bulk_notification(discord_msg))

    return {"count": len(updated), "players": updated}


# ---------------------------------------------------------------------------
# POST /api/admin/bulk-release
# ---------------------------------------------------------------------------
@router.post("/bulk-release")
async def bulk_release(request: Request, _=Depends(verify_api_key)):
    body = await request.json()
    admin = body.get("admin", "unknown")
    upids = body.get("upids", [])
    reason = body.get("reason", "Admin bulk release")

    if not upids:
        raise HTTPException(status_code=400, detail="No UPIDs provided")

    players = load_json(COMBINED_FILE)
    released = []

    for p in players:
        if str(p.get("upid")) in [str(u) for u in upids]:
            old_owner = p.get("manager", "")
            old_contract = p.get("years_simple", "")
            old_type = p.get("contract_type", "")

            p["manager"] = ""
            p["FBP_Team"] = "" if "FBP_Team" in p else p.get("FBP_Team")
            p["contract_type"] = ""
            p["years_simple"] = ""

            log_player_action(
                upid=p.get("upid"),
                player_name=p.get("name", "Unknown"),
                update_type="Drop",
                event=f"Bulk released by {admin}: {reason}",
                admin=admin,
                owner=old_owner,
                changes={
                    "manager": {"from": old_owner, "to": ""},
                    "contract_type": {"from": old_type, "to": ""},
                    "years_simple": {"from": old_contract, "to": ""},
                },
            )
            released.append({"name": p.get("name", "Unknown"), "former_owner": old_owner})

    save_json(COMBINED_FILE, players)

    commit_msg = f"Admin bulk release: {len(released)} players ({reason})"
    _enqueue_commit([COMBINED_FILE, PLAYER_LOG_FILE], commit_msg)
    sync_to_website([COMBINED_FILE, PLAYER_LOG_FILE], commit_msg)

    # Discord notification
    if _bot_ref and released:
        player_names = ', '.join([p['name'] for p in released[:5]])
        if len(released) > 5:
            player_names += f' (+{len(released) - 5} more)'
        discord_msg = f"🔓 **Bulk Release**\n\n👥 {len(released)} players released\n📝 Reason: {reason}\n📝 Players: {player_names}\n👤 Admin: {admin}\n💾 Source: Website Admin Portal"
        _bot_ref.loop.create_task(_send_admin_bulk_notification(discord_msg))

    return {"count": len(released), "players": released}


# ---------------------------------------------------------------------------
# POST /api/admin/add-player
# ---------------------------------------------------------------------------
@router.post("/add-player")
async def add_player(request: Request, _=Depends(verify_api_key)):
    """
    Add a new player to the database.

    Creates:
    - A new UPID + upid_database.json entry
    - A new player in combined_players.json
    - A player_log.json entry
    - Queues a git commit/push via the centralised worker
    """
    body = await request.json()
    admin = body.get("admin", "unknown")
    player_data = body.get("player_data", {})

    if not player_data.get("name"):
        raise HTTPException(status_code=400, detail="Player name is required")

    try:
        print(f"🔄 Starting add-player for {player_data.get('name')} by {admin}")

        # --- Generate UPID ---
        upid_db = load_json(UPID_DB_FILE)
        if not upid_db or "by_upid" not in upid_db:
            upid_db = {"by_upid": {}, "name_index": {}}

        existing_upids = [int(u) for u in upid_db["by_upid"].keys() if u.isdigit()]
        next_upid = (max(existing_upids) + 1) if existing_upids else 1
        player_data["upid"] = str(next_upid)

        print(f"  📝 Assigned UPID: {next_upid}")

        # --- Add to combined_players.json ---
        players = load_json(COMBINED_FILE)
        if not isinstance(players, list):
            players = []

        new_player = {
            "upid": str(next_upid),
            "name": player_data.get("name", ""),
            "team": player_data.get("team", ""),
            "position": player_data.get("position", ""),
            "age": player_data.get("age"),
            "manager": player_data.get("manager", ""),
            "player_type": player_data.get("player_type", "Farm"),
            "contract_type": player_data.get("contract_type", ""),
            "years_simple": player_data.get("years_simple", ""),
            "yahoo_id": player_data.get("yahoo_id", ""),
            "mlb_id": player_data.get("mlb_id", ""),
            "birth_date": player_data.get("birth_date"),
            "debut_date": player_data.get("debut_date"),
            "bats": player_data.get("bats", ""),
            "throws": player_data.get("throws", ""),
            "fypd": player_data.get("fypd", False),
            "level": player_data.get("level", ""),
        }
        players.append(new_player)
        save_json(COMBINED_FILE, players)
        print(f"  💾 Saved to combined_players.json")

        # --- Add to upid_database.json ---
        alt_names = player_data.get("alt_names", [])
        upid_db["by_upid"][str(next_upid)] = {
            "upid": str(next_upid),
            "name": player_data.get("name", ""),
            "team": player_data.get("team", ""),
            "pos": player_data.get("position", ""),
            "alt_names": alt_names,
            "approved_dupes": "FALSE",
        }

        # Update name index
        all_names = [player_data.get("name", "")] + alt_names
        for name in all_names:
            key = name.lower().strip()
            if key:
                upid_db["name_index"].setdefault(key, []).append(str(next_upid))

        save_json(UPID_DB_FILE, upid_db)
        print(f"  💾 Saved to upid_database.json")

        # --- Log ---
        log_player_action(
            upid=next_upid,
            player_name=player_data.get("name", "Unknown"),
            update_type="Admin",
            event=f"Player added to database by {admin}",
            admin=admin,
            owner=player_data.get("manager", ""),
            changes={"action": "new_player", "all_fields": player_data},
        )
        print(f"  💾 Saved to player_log.json")

        # --- Queue git commit (best-effort; data is already persisted to disk) ---
        commit_msg = f"Admin: Add player {player_data.get('name', '?')} (UPID: {next_upid})"
        _enqueue_commit([COMBINED_FILE, UPID_DB_FILE, PLAYER_LOG_FILE], commit_msg)

        # --- Sync to website ---
        try:
            sync_to_website([COMBINED_FILE, UPID_DB_FILE, PLAYER_LOG_FILE], commit_msg)
            print(f"  ✅ Synced to website")
        except Exception as sync_error:
            print(f"  ⚠️ Website sync failed: {sync_error} (Player IS saved locally)")

        # --- Discord notification ---
        if _bot_ref:
            try:
                player_name = player_data.get('name', 'Unknown')
                team = player_data.get('team', 'N/A')
                position = player_data.get('position', 'N/A')
                discord_msg = f"➕ **New Player Added**\n\n👤 Player: **{player_name}**\n⚾ Team: {team}\n🎯 Position: {position}\n🆔 UPID: {next_upid}\n👤 Admin: {admin}\n💾 Source: Website Admin Portal"
                _bot_ref.loop.create_task(_send_admin_bulk_notification(discord_msg))
                print(f"  📢 Discord notification scheduled")
            except Exception as discord_error:
                print(f"  ⚠️ Discord notification failed: {discord_error}")

        print(f"✅ Add-player complete: {player_data.get('name')} with UPID {next_upid}")

        return {
            "success": True,
            "upid": next_upid,
            "player": new_player,
            "message": f"Player {player_data.get('name')} successfully added with UPID {next_upid}"
        }

    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Unexpected error during add-player: {str(e)}"
        print(f"❌ {error_msg}")
        raise HTTPException(
            status_code=500,
            detail=f"Add player failed: {error_msg}. Please try again or contact admin."
        )


# ---------------------------------------------------------------------------
# POST /api/admin/enrich-player
# ---------------------------------------------------------------------------
@router.post("/enrich-player")
async def enrich_player(request: Request, _=Depends(verify_api_key)):
    """
    Search MLB Stats API for a player by name, return bio data.
    No Yahoo/Fangraphs for now — MLB API is free and reliable.
    """
    body = await request.json()
    name = body.get("name", "").strip()
    team_hint = body.get("team")

    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    enriched = {}

    # --- MLB Stats API search ---
    try:
        search_url = f"https://statsapi.mlb.com/api/v1/people/search?names={requests.utils.quote(name)}&hydrate=currentTeam"
        resp = requests.get(search_url, timeout=10)

        if resp.status_code == 200:
            people = resp.json().get("people", [])

            # If team hint provided, prefer that match
            best = None
            for person in people:
                if team_hint:
                    current_team = person.get("currentTeam", {}).get("abbreviation", "")
                    if current_team.upper() == team_hint.upper():
                        best = person
                        break

                if not best:
                    best = person  # Take first result as fallback

            if best:
                enriched["mlb_id"] = str(best.get("id", ""))
                enriched["birth_date"] = best.get("birthDate")
                enriched["debut_date"] = best.get("mlbDebutDate")
                enriched["bats"] = best.get("batSide", {}).get("code")
                enriched["throws"] = best.get("pitchHand", {}).get("code")
                enriched["position"] = best.get("primaryPosition", {}).get("abbreviation")
                enriched["team"] = best.get("currentTeam", {}).get("abbreviation")
                enriched["age"] = best.get("currentAge")

    except Exception as e:
        print(f"⚠️ MLB API search error: {e}")

    # Filter out None values
    enriched = {k: v for k, v in enriched.items() if v is not None}

    return enriched
