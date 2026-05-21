from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

import discord
import requests
from discord.ui import Button, View
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from data_lock import DATA_LOCK
from pad.pad_processor import _append_player_log_entry
from team_utils import load_managers_config, normalize_team_abbr


router = APIRouter(prefix="/api/manager", tags=["manager-player"])

API_KEY = os.getenv("BOT_API_KEY", "")

COMBINED_FILE = "data/combined_players.json"
UPID_DB_FILE = "data/upid_database.json"
PLAYER_LOG_FILE = "data/player_log.json"
PLAYER_ADD_REQUESTS_FILE = "data/player_add_requests.json"

ADMIN_LOG_CHANNEL_ID = int(os.getenv("ADMIN_LOG_CHANNEL_ID", "1079466810375688262"))
ADMIN_TASKS_CHANNEL_ID = int(os.getenv("ADMIN_TASKS_CHANNEL_ID", "875594022033436683"))

ALLOWED_EDIT_FIELDS = {
    "age",
    "bats",
    "birth_date",
    "debut_date",
    "debuted",
    "height",
    "mlb_id",
    "mlb_primary_position",
    "name",
    "position",
    "team",
    "throws",
    "weight",
    "yahoo_id",
}

INT_FIELDS = {"age", "mlb_id", "weight"}
BOOL_FIELDS = {"debuted"}
PROOF_ALLOWED_DOMAINS = (
    "baseball-reference.com",
    "mlb.com",
    "fangraphs.com",
)

REQUEST_ID_RE = re.compile(r"Request ID:\s*`([^`]+)`")

_bot_ref = None
_commit_fn: Optional[Callable[[list[str], str], None]] = None
_persistent_view_registered = False


class ManagerPlayerUpdatePayload(BaseModel):
    upid: str
    changes: Dict[str, Any] = Field(default_factory=dict)
    alt_names_to_add: list[str] = Field(default_factory=list)


class ManagerAddPlayerRequestPayload(BaseModel):
    player_data: Dict[str, Any] = Field(default_factory=dict)


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


def set_manager_players_bot_reference(bot) -> None:
    global _bot_ref, _persistent_view_registered
    _bot_ref = bot
    if _persistent_view_registered:
        return
    try:
        bot.add_view(PlayerAddRequestReviewView())
        _persistent_view_registered = True
    except Exception as exc:
        print(f"⚠️ Failed to register manager player review view: {exc}")


def set_manager_players_commit_fn(fn: Callable[[list[str], str], None]) -> None:
    global _commit_fn
    _commit_fn = fn


def _enqueue_commit(files: list[str], message: str) -> None:
    if _commit_fn is None:
        print(f"⚠️ No commit function configured – skipping: {message}")
        return
    _commit_fn(files, message)


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if data is not None else default


def _save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _ensure_upid_db(raw: dict | None) -> dict:
    data = raw if isinstance(raw, dict) else {}
    by_upid = data.get("by_upid")
    name_index = data.get("name_index")
    if not isinstance(by_upid, dict):
        by_upid = {}
    if not isinstance(name_index, dict):
        name_index = {}
    return {"by_upid": by_upid, "name_index": name_index}


def _load_add_requests() -> dict:
    data = _load_json(PLAYER_ADD_REQUESTS_FILE, {"requests": {}})
    if not isinstance(data, dict):
        return {"requests": {}}
    requests_data = data.get("requests")
    if not isinstance(requests_data, dict):
        data["requests"] = {}
    return data


def _save_add_requests(data: dict) -> None:
    if not isinstance(data, dict):
        data = {"requests": {}}
    if not isinstance(data.get("requests"), dict):
        data["requests"] = {}
    _save_json(PLAYER_ADD_REQUESTS_FILE, data)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _coerce_int(field: str, value: Any) -> Optional[int]:
    raw = _clean_text(value)
    if raw == "":
        return None
    try:
        return int(raw)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Field '{field}' must be an integer")


def _coerce_bool(field: str, value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raw = _clean_text(value).lower()
    if raw == "":
        return None
    if raw in {"true", "1", "yes", "y"}:
        return True
    if raw in {"false", "0", "no", "n"}:
        return False
    raise HTTPException(status_code=400, detail=f"Field '{field}' must be boolean")


def _normalize_edit_value(field: str, value: Any) -> Any:
    if field in INT_FIELDS:
        return _coerce_int(field, value)
    if field in BOOL_FIELDS:
        return _coerce_bool(field, value)

    if value is None:
        return None

    text = _clean_text(value)

    if field in {"team", "bats", "throws", "position", "mlb_primary_position"}:
        return text.upper()

    if field in {"birth_date", "debut_date"}:
        return text or None

    if field in {"name", "height", "yahoo_id"}:
        return text

    return value


def _parse_alt_names(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        candidates = re.split(r"[\n,]+", raw)
    elif isinstance(raw, list):
        candidates = [str(x) for x in raw]
    else:
        return []

    cleaned: list[str] = []
    seen: set[str] = set()
    for cand in candidates:
        val = _clean_text(cand)
        key = val.lower()
        if not val or key in seen:
            continue
        cleaned.append(val)
        seen.add(key)
    return cleaned


def _rebuild_name_index(upid_db: dict) -> None:
    name_index: dict[str, list[str]] = {}
    by_upid = upid_db.get("by_upid", {})
    for upid, rec in by_upid.items():
        if not isinstance(rec, dict):
            continue
        names = [_clean_text(rec.get("name"))] + _parse_alt_names(rec.get("alt_names"))
        for name in names:
            key = name.lower()
            if not key:
                continue
            name_index.setdefault(key, []).append(str(upid))
    upid_db["name_index"] = name_index


def _build_change_lines(changes: dict[str, dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for field in sorted(changes.keys()):
        change = changes.get(field) or {}
        before = change.get("from")
        after = change.get("to")
        before_s = "∅" if before in (None, "") else str(before)
        after_s = "∅" if after in (None, "") else str(after)
        lines.append(f"- `{field}`: {before_s} → {after_s}")
    return lines


def _schedule_on_bot_loop(coro, label: str) -> None:
    if _bot_ref is None:
        return
    loop = getattr(_bot_ref, "loop", None)
    if loop is None or not loop.is_running():
        return

    fut = asyncio.run_coroutine_threadsafe(coro, loop)

    def _done(done_fut):
        try:
            done_fut.result()
        except Exception as exc:
            print(f"⚠️ Discord task failed ({label}): {exc}")

    fut.add_done_callback(_done)


async def _await_on_bot_loop(coro, label: str, timeout_s: float = 20.0):
    if _bot_ref is None:
        raise HTTPException(status_code=503, detail="Bot not ready")

    loop = getattr(_bot_ref, "loop", None)
    if loop is None or not loop.is_running():
        raise HTTPException(status_code=503, detail="Bot loop not ready")

    try:
        if asyncio.get_running_loop() == loop:
            return await coro
    except RuntimeError:
        pass

    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return await asyncio.wait_for(asyncio.wrap_future(fut), timeout=timeout_s)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"Discord operation timed out: {label}")


async def _fetch_channel(channel_id: int):
    if _bot_ref is None:
        return None
    channel = _bot_ref.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        return await _bot_ref.fetch_channel(channel_id)
    except Exception:
        return None


async def _send_edit_log_message(message: str) -> None:
    channel = await _fetch_channel(ADMIN_LOG_CHANNEL_ID)
    if channel is None:
        print(f"⚠️ Admin log channel not found: {ADMIN_LOG_CHANNEL_ID}")
        return
    await channel.send(message)


def _validate_proof_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="Proof URL must use http(s)")

    host = (parsed.hostname or "").lower()
    if not host:
        raise HTTPException(status_code=400, detail="Proof URL host is required")

    allowed = False
    for domain in PROOF_ALLOWED_DOMAINS:
        if host == domain or host.endswith(f".{domain}"):
            allowed = True
            break
    if not allowed:
        raise HTTPException(
            status_code=400,
            detail="Proof URL must be from baseball-reference.com, mlb.com, or fangraphs.com",
        )


def _find_duplicate_upids(upid_db: dict, candidate_names: list[str]) -> list[dict]:
    by_upid = upid_db.get("by_upid", {})
    name_index = upid_db.get("name_index", {})

    hits: dict[str, dict] = {}
    for name in candidate_names:
        key = _clean_text(name).lower()
        if not key:
            continue
        for upid in name_index.get(key, []):
            rec = by_upid.get(str(upid))
            if not isinstance(rec, dict):
                continue
            hits[str(upid)] = {
                "upid": str(upid),
                "name": rec.get("name", ""),
                "team": rec.get("team", ""),
                "pos": rec.get("pos", ""),
                "alt_names": _parse_alt_names(rec.get("alt_names")),
            }
    return sorted(hits.values(), key=lambda r: int(r.get("upid", "0")) if str(r.get("upid", "")).isdigit() else 0)


def _enrich_player_data(name: str, team_hint: Optional[str]) -> dict:
    enriched: dict[str, Any] = {}
    search_url = (
        "https://statsapi.mlb.com/api/v1/people/search"
        f"?names={requests.utils.quote(name)}&hydrate=currentTeam"
    )
    try:
        resp = requests.get(search_url, timeout=10)
    except Exception as exc:
        print(f"⚠️ MLB API request failed while enriching player '{name}': {exc}")
        return enriched

    if resp.status_code != 200:
        return enriched

    people = (resp.json() or {}).get("people", [])
    if not isinstance(people, list) or not people:
        return enriched

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
        return enriched

    enriched["mlb_id"] = best.get("id")
    enriched["birth_date"] = best.get("birthDate")
    enriched["debut_date"] = best.get("mlbDebutDate")
    enriched["bats"] = (best.get("batSide") or {}).get("code")
    enriched["throws"] = (best.get("pitchHand") or {}).get("code")
    enriched["position"] = (best.get("primaryPosition") or {}).get("abbreviation")
    enriched["mlb_primary_position"] = (best.get("primaryPosition") or {}).get("abbreviation")
    enriched["team"] = (best.get("currentTeam") or {}).get("abbreviation")
    enriched["age"] = best.get("currentAge")
    return {k: v for k, v in enriched.items() if v not in (None, "")}


def _generate_request_id(existing: dict[str, dict], manager_team: str) -> str:
    while True:
        token = uuid.uuid4().hex[:6].upper()
        candidate = f"PAR-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{manager_team}-{token}"
        if candidate not in existing:
            return candidate


def _render_request_card(record: dict) -> str:
    player_data = record.get("player_data") or {}
    duplicates = record.get("duplicate_matches") or []
    enrichment = record.get("enrichment") or {}
    status = str(record.get("status") or "pending").lower()
    review = record.get("review") or {}

    duplicate_text = "None"
    if duplicates:
        duplicate_lines = [
            f"- UPID {d.get('upid')}: {d.get('name')} ({d.get('team')}/{d.get('pos')})"
            for d in duplicates[:8]
        ]
        if len(duplicates) > 8:
            duplicate_lines.append(f"- (+{len(duplicates) - 8} more)")
        duplicate_text = "\n".join(duplicate_lines)

    enrich_bits = []
    if enrichment.get("mlb_id") is not None:
        enrich_bits.append(f"MLB ID {enrichment.get('mlb_id')}")
    if enrichment.get("yahoo_id") not in (None, ""):
        enrich_bits.append(f"Yahoo ID {enrichment.get('yahoo_id')}")
    enrich_text = ", ".join(enrich_bits) if enrich_bits else "No enrichment match"

    status_line = "⏳ Pending"
    if status == "approved":
        approved_upid = (review.get("approved_player") or {}).get("upid")
        status_line = f"✅ Approved by {review.get('reviewed_by', 'admin')}"
        if approved_upid:
            status_line += f" (UPID {approved_upid})"
    elif status == "rejected":
        status_line = f"❌ Rejected by {review.get('reviewed_by', 'admin')}"
    elif status == "delivery_failed":
        status_line = "⚠️ Delivery failed (request saved)"

    alt_names = _parse_alt_names(player_data.get("alt_names"))
    alt_names_text = ", ".join(alt_names) if alt_names else "(none)"

    return (
        "🆕 **Manager Add Player Request**\n"
        f"Request ID: `{record.get('request_id')}`\n"
        f"Status: {status_line}\n"
        f"Manager Team: `{record.get('manager_team')}`\n"
        f"Submitted: {record.get('submitted_at')}\n\n"
        f"Player: **{player_data.get('name', '')}**\n"
        f"Team / Position: `{player_data.get('team', '')}` / `{player_data.get('position', '')}`\n"
        f"Player Type: `{player_data.get('player_type', 'Farm')}`\n"
        f"Proof: {record.get('proof_url')}\n"
        f"Alt Names: {alt_names_text}\n\n"
        f"Enrichment: {enrich_text}\n"
        f"Duplicate Matches:\n{duplicate_text}\n\n"
        "Use buttons below to approve or reject."
    )


def _disabled_review_view() -> "PlayerAddRequestReviewView":
    view = PlayerAddRequestReviewView()
    for item in view.children:
        item.disabled = True
    return view


def _get_admin_team_for_discord_user(discord_id: int) -> Optional[str]:
    cfg = load_managers_config()
    teams = cfg.get("teams") if isinstance(cfg.get("teams"), dict) else {}
    for abbr, meta in teams.items():
        if not isinstance(meta, dict):
            continue
        if str(meta.get("discord_id") or "").strip() != str(discord_id):
            continue
        role = str(meta.get("role") or "").strip().lower()
        if role == "admin":
            return str(abbr).upper()
    return None


def _extract_request_id_from_message(content: str) -> Optional[str]:
    match = REQUEST_ID_RE.search(content or "")
    if not match:
        return None
    return match.group(1).strip()


def _find_request_id_by_message_id(message_id: int | str) -> Optional[str]:
    target = str(message_id)
    with DATA_LOCK:
        store = _load_add_requests()
        for request_id, rec in (store.get("requests") or {}).items():
            discord_meta = rec.get("discord") or {}
            if str(discord_meta.get("message_id") or "") == target:
                return request_id
    return None


def _normalize_add_player_data(raw_player_data: dict) -> dict:
    name = _clean_text(raw_player_data.get("name"))
    if not name:
        raise HTTPException(status_code=400, detail="Player name is required")

    proof_url = _clean_text(raw_player_data.get("proof_url"))
    if not proof_url:
        raise HTTPException(status_code=400, detail="Proof URL is required")
    _validate_proof_url(proof_url)

    team = _clean_text(raw_player_data.get("team")).upper()
    position = _clean_text(raw_player_data.get("position")).upper()
    player_type = _clean_text(raw_player_data.get("player_type")) or "Farm"
    if player_type not in {"MLB", "Farm"}:
        player_type = "Farm"

    alt_names = _parse_alt_names(raw_player_data.get("alt_names"))

    normalized = {
        "name": name,
        "team": team,
        "position": position,
        "player_type": player_type,
        "proof_url": proof_url,
        "alt_names": alt_names,
        "age": _coerce_int("age", raw_player_data.get("age")),
        "mlb_id": _coerce_int("mlb_id", raw_player_data.get("mlb_id")),
        "weight": _coerce_int("weight", raw_player_data.get("weight")),
        "bats": _clean_text(raw_player_data.get("bats")).upper(),
        "throws": _clean_text(raw_player_data.get("throws")).upper(),
        "birth_date": _clean_text(raw_player_data.get("birth_date")) or None,
        "debut_date": _clean_text(raw_player_data.get("debut_date")) or None,
        "debuted": _coerce_bool("debuted", raw_player_data.get("debuted")),
        "height": _clean_text(raw_player_data.get("height")),
        "mlb_primary_position": _clean_text(raw_player_data.get("mlb_primary_position")).upper(),
        "yahoo_id": _clean_text(raw_player_data.get("yahoo_id")),
    }
    return normalized


def _build_new_player_record(player_data: dict, next_upid: int) -> dict:
    return {
        "upid": str(next_upid),
        "name": player_data.get("name", ""),
        "team": player_data.get("team", ""),
        "position": player_data.get("position", ""),
        "age": player_data.get("age"),
        "manager": "",
        "FBP_Team": "",
        "player_type": player_data.get("player_type", "Farm"),
        "contract_type": "",
        "years_simple": "",
        "yahoo_id": player_data.get("yahoo_id", ""),
        "mlb_id": player_data.get("mlb_id"),
        "birth_date": player_data.get("birth_date"),
        "debut_date": player_data.get("debut_date"),
        "debuted": player_data.get("debuted"),
        "bats": player_data.get("bats", ""),
        "throws": player_data.get("throws", ""),
        "height": player_data.get("height", ""),
        "weight": player_data.get("weight"),
        "mlb_primary_position": player_data.get("mlb_primary_position", ""),
        "fypd": False,
        "level": "",
    }


def _create_player_from_request_record(record: dict, admin_team: str) -> dict:
    player_data = dict(record.get("player_data") or {})

    players = _load_json(COMBINED_FILE, [])
    if not isinstance(players, list):
        players = []

    upid_db = _ensure_upid_db(_load_json(UPID_DB_FILE, {"by_upid": {}, "name_index": {}}))
    by_upid = upid_db.get("by_upid", {})

    existing_upids = [int(k) for k in by_upid.keys() if str(k).isdigit()]
    next_upid = (max(existing_upids) + 1) if existing_upids else 1
    while str(next_upid) in by_upid:
        next_upid += 1

    new_player = _build_new_player_record(player_data, next_upid)
    players.append(new_player)
    _save_json(COMBINED_FILE, players)

    alt_names = _parse_alt_names(player_data.get("alt_names"))
    by_upid[str(next_upid)] = {
        "upid": str(next_upid),
        "name": new_player.get("name", ""),
        "team": new_player.get("team", ""),
        "pos": new_player.get("position", ""),
        "alt_names": alt_names,
        "approved_dupes": "FALSE",
    }
    _rebuild_name_index(upid_db)
    _save_json(UPID_DB_FILE, upid_db)

    season = datetime.now().year
    player_log = _load_json(PLAYER_LOG_FILE, [])
    if not isinstance(player_log, list):
        player_log = []
    _append_player_log_entry(
        player_log,
        new_player,
        season=season,
        source="Manager Add Request",
        update_type="Manager Add",
        event=f"Player added via manager request approved by {admin_team}",
        admin=admin_team,
    )
    _save_json(PLAYER_LOG_FILE, player_log)

    return new_player


def _approve_add_request(request_id: str, admin_team: str) -> tuple[dict, dict]:
    with DATA_LOCK:
        store = _load_add_requests()
        requests_data = store.get("requests") or {}
        record = requests_data.get(request_id)
        if not isinstance(record, dict):
            raise ValueError(f"Request {request_id} not found")

        status = str(record.get("status") or "pending").lower()
        if status != "pending":
            raise ValueError(f"Request already processed ({status})")

        new_player = _create_player_from_request_record(record, admin_team)

        reviewed_at = datetime.now(timezone.utc).isoformat()
        record["status"] = "approved"
        record["review"] = {
            "reviewed_by": admin_team,
            "reviewed_at": reviewed_at,
            "approved_player": {
                "upid": new_player.get("upid"),
                "name": new_player.get("name"),
            },
        }
        requests_data[request_id] = record
        store["requests"] = requests_data
        _save_add_requests(store)

        commit_msg = f"Manager add approved: {new_player.get('name', '?')} ({admin_team})"
        _enqueue_commit(
            [COMBINED_FILE, UPID_DB_FILE, PLAYER_LOG_FILE, PLAYER_ADD_REQUESTS_FILE],
            commit_msg,
        )

    return record, new_player


def _reject_add_request(request_id: str, admin_team: str) -> dict:
    with DATA_LOCK:
        store = _load_add_requests()
        requests_data = store.get("requests") or {}
        record = requests_data.get(request_id)
        if not isinstance(record, dict):
            raise ValueError(f"Request {request_id} not found")

        status = str(record.get("status") or "pending").lower()
        if status != "pending":
            raise ValueError(f"Request already processed ({status})")

        reviewed_at = datetime.now(timezone.utc).isoformat()
        record["status"] = "rejected"
        record["review"] = {
            "reviewed_by": admin_team,
            "reviewed_at": reviewed_at,
        }
        requests_data[request_id] = record
        store["requests"] = requests_data
        _save_add_requests(store)
        _enqueue_commit(
            [PLAYER_ADD_REQUESTS_FILE],
            f"Manager add rejected: {request_id} ({admin_team})",
        )

    return record


class PlayerAddRequestReviewView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="✅ Approve Player Add",
        style=discord.ButtonStyle.success,
        custom_id="manager_player_add_approve",
    )
    async def approve(self, interaction: discord.Interaction, button: Button):
        admin_team = _get_admin_team_for_discord_user(interaction.user.id)
        if not admin_team:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        request_id = _extract_request_id_from_message(interaction.message.content or "")
        if not request_id:
            request_id = _find_request_id_by_message_id(interaction.message.id)
        if not request_id:
            await interaction.response.send_message("Could not resolve request ID.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            record, new_player = _approve_add_request(request_id, admin_team)
        except ValueError as exc:
            await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            return
        except Exception as exc:
            await interaction.followup.send(f"❌ Approval failed: {exc}", ephemeral=True)
            return

        try:
            await interaction.message.edit(
                content=_render_request_card(record),
                view=_disabled_review_view(),
            )
        except Exception as exc:
            print(f"⚠️ Failed to edit add-request review card after approval: {exc}")

        player_name = new_player.get("name", "Unknown")
        upid = new_player.get("upid", "?")
        log_message = (
            "✅ **Manager Add Approved**\n\n"
            f"Player: **{player_name}** (UPID {upid})\n"
            f"Request ID: `{request_id}`\n"
            f"Approved by: `{admin_team}`"
        )
        _schedule_on_bot_loop(_send_edit_log_message(log_message), "manager_add_approved_log")

        await interaction.followup.send(
            f"Approved {player_name} (UPID {upid}).",
            ephemeral=True,
        )

    @discord.ui.button(
        label="❌ Reject",
        style=discord.ButtonStyle.danger,
        custom_id="manager_player_add_reject",
    )
    async def reject(self, interaction: discord.Interaction, button: Button):
        admin_team = _get_admin_team_for_discord_user(interaction.user.id)
        if not admin_team:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        request_id = _extract_request_id_from_message(interaction.message.content or "")
        if not request_id:
            request_id = _find_request_id_by_message_id(interaction.message.id)
        if not request_id:
            await interaction.response.send_message("Could not resolve request ID.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            record = _reject_add_request(request_id, admin_team)
        except ValueError as exc:
            await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            return
        except Exception as exc:
            await interaction.followup.send(f"❌ Rejection failed: {exc}", ephemeral=True)
            return

        try:
            await interaction.message.edit(
                content=_render_request_card(record),
                view=_disabled_review_view(),
            )
        except Exception as exc:
            print(f"⚠️ Failed to edit add-request review card after rejection: {exc}")

        await interaction.followup.send("Request rejected.", ephemeral=True)


async def _post_add_request_review(record: dict) -> dict:
    channel = await _fetch_channel(ADMIN_TASKS_CHANNEL_ID)
    if channel is None:
        raise HTTPException(status_code=503, detail="Admin review channel unavailable")

    msg = await channel.send(
        content=_render_request_card(record),
        view=PlayerAddRequestReviewView(),
    )
    return {
        "channel_id": str(channel.id),
        "message_id": str(msg.id),
        "posted_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/player-update")
async def manager_player_update(
    payload: ManagerPlayerUpdatePayload,
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    changes_raw = payload.changes or {}
    invalid_fields = set(changes_raw.keys()) - ALLOWED_EDIT_FIELDS
    if invalid_fields:
        invalid_csv = ", ".join(sorted(invalid_fields))
        raise HTTPException(status_code=400, detail=f"Invalid edit fields: {invalid_csv}")

    normalized_changes: dict[str, Any] = {}
    for field, value in changes_raw.items():
        normalized_changes[field] = _normalize_edit_value(field, value)

    alt_names_to_add = _parse_alt_names(payload.alt_names_to_add)
    if not normalized_changes and not alt_names_to_add:
        raise HTTPException(status_code=400, detail="No changes provided")

    upid = _clean_text(payload.upid)
    if not upid:
        raise HTTPException(status_code=400, detail="UPID is required")

    with DATA_LOCK:
        players = _load_json(COMBINED_FILE, [])
        if not isinstance(players, list):
            players = []

        player = next((p for p in players if _clean_text(p.get("upid")) == upid), None)
        if not isinstance(player, dict):
            raise HTTPException(status_code=404, detail=f"Player with UPID {upid} not found")

        changes_applied: dict[str, dict[str, Any]] = {}
        for field, value in normalized_changes.items():
            before = player.get(field)
            if before != value:
                player[field] = value
                changes_applied[field] = {"from": before, "to": value}

        alt_names_added: list[str] = []
        upid_db_changed = False
        if alt_names_to_add or any(k in {"name", "team", "position"} for k in changes_applied.keys()):
            upid_db = _ensure_upid_db(_load_json(UPID_DB_FILE, {"by_upid": {}, "name_index": {}}))
            by_upid = upid_db.get("by_upid", {})
            rec = by_upid.get(upid)
            if not isinstance(rec, dict):
                rec = {
                    "upid": upid,
                    "name": player.get("name", ""),
                    "team": player.get("team", ""),
                    "pos": player.get("position", ""),
                    "alt_names": [],
                    "approved_dupes": "FALSE",
                }
                by_upid[upid] = rec

            rec["name"] = player.get("name", rec.get("name", ""))
            rec["team"] = player.get("team", rec.get("team", ""))
            rec["pos"] = player.get("position", rec.get("pos", ""))

            existing_keys = {
                _clean_text(rec.get("name")).lower(),
                _clean_text(player.get("name")).lower(),
            }
            current_alt_names = _parse_alt_names(rec.get("alt_names"))
            for alt in current_alt_names:
                existing_keys.add(alt.lower())

            for alt in alt_names_to_add:
                key = alt.lower()
                if not key or key in existing_keys:
                    continue
                current_alt_names.append(alt)
                alt_names_added.append(alt)
                existing_keys.add(key)

            rec["alt_names"] = current_alt_names
            _rebuild_name_index(upid_db)
            _save_json(UPID_DB_FILE, upid_db)
            upid_db_changed = True

        if not changes_applied and not alt_names_added:
            raise HTTPException(status_code=400, detail="No effective changes detected")

        season = datetime.now().year
        player_log = _load_json(PLAYER_LOG_FILE, [])
        if not isinstance(player_log, list):
            player_log = []

        event_parts = []
        if changes_applied:
            event_parts.append("fields: " + ", ".join(sorted(changes_applied.keys())))
        if alt_names_added:
            event_parts.append("alt names added: " + ", ".join(alt_names_added))
        event = f"Manager {manager_team} updated player ({'; '.join(event_parts)})"
        _append_player_log_entry(
            player_log,
            player,
            season=season,
            source="Manager Player Database",
            update_type="Manager Edit",
            event=event,
            admin=manager_team,
        )

        _save_json(COMBINED_FILE, players)
        _save_json(PLAYER_LOG_FILE, player_log)

        files = [COMBINED_FILE, PLAYER_LOG_FILE]
        if upid_db_changed:
            files.append(UPID_DB_FILE)
        _enqueue_commit(files, f"Manager edit: {player.get('name', upid)} ({manager_team})")

    change_lines = _build_change_lines(changes_applied)
    if alt_names_added:
        change_lines.append(f"- `alt_names`: +{', '.join(alt_names_added)}")
    changes_block = "\n".join(change_lines) if change_lines else "- (no field changes)"
    log_message = (
        "🛠️ **Manager Player Edit**\n\n"
        f"Manager: `{manager_team}`\n"
        f"Player: **{player.get('name', 'Unknown')}** (UPID {upid})\n"
        f"Changes:\n{changes_block}"
    )
    _schedule_on_bot_loop(_send_edit_log_message(log_message), "manager_player_edit_log")

    return {
        "success": True,
        "player": player,
        "changes": changes_applied,
        "alt_names_added": alt_names_added,
    }


@router.post("/add-player-request")
async def manager_add_player_request(
    payload: ManagerAddPlayerRequestPayload,
    _: bool = Depends(verify_key),
    manager_team: str = Depends(require_manager_team),
):
    raw_player_data = dict(payload.player_data or {})
    normalized = _normalize_add_player_data(raw_player_data)

    name = normalized.get("name", "")
    team_hint = normalized.get("team") or None
    enrichment = _enrich_player_data(name, team_hint)
    for field, value in enrichment.items():
        if field not in normalized or normalized.get(field) in (None, ""):
            normalized[field] = value

    candidate_names = [normalized.get("name", "")] + _parse_alt_names(normalized.get("alt_names"))
    with DATA_LOCK:
        upid_db = _ensure_upid_db(_load_json(UPID_DB_FILE, {"by_upid": {}, "name_index": {}}))
        duplicate_matches = _find_duplicate_upids(upid_db, candidate_names)

    submitted_at = datetime.now(timezone.utc).isoformat()

    with DATA_LOCK:
        store = _load_add_requests()
        requests_data = store.get("requests") or {}
        request_id = _generate_request_id(requests_data, manager_team)

        record = {
            "request_id": request_id,
            "status": "pending",
            "submitted_at": submitted_at,
            "manager_team": manager_team,
            "proof_url": normalized.get("proof_url", ""),
            "player_data": normalized,
            "duplicate_matches": duplicate_matches,
            "enrichment": enrichment,
            "review": {},
            "discord": {},
        }
        requests_data[request_id] = record
        store["requests"] = requests_data
        _save_add_requests(store)
        _enqueue_commit(
            [PLAYER_ADD_REQUESTS_FILE],
            f"Manager add request: {normalized.get('name', '?')} ({manager_team})",
        )

    try:
        discord_meta = await _await_on_bot_loop(
            _post_add_request_review(record),
            "post_add_player_review",
            timeout_s=15.0,
        )
    except Exception as exc:
        with DATA_LOCK:
            store = _load_add_requests()
            requests_data = store.get("requests") or {}
            rec = requests_data.get(request_id)
            if isinstance(rec, dict):
                rec["status"] = "delivery_failed"
                rec["review"] = {
                    "error": str(exc),
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                }
                requests_data[request_id] = rec
                store["requests"] = requests_data
                _save_add_requests(store)
                _enqueue_commit(
                    [PLAYER_ADD_REQUESTS_FILE],
                    f"Manager add request delivery failed: {request_id}",
                )
        raise HTTPException(
            status_code=503,
            detail="Request saved, but posting to admin review channel failed",
        )

    with DATA_LOCK:
        store = _load_add_requests()
        requests_data = store.get("requests") or {}
        rec = requests_data.get(request_id)
        if isinstance(rec, dict):
            rec["discord"] = discord_meta
            requests_data[request_id] = rec
            store["requests"] = requests_data
            _save_add_requests(store)

    return {
        "success": True,
        "request_id": request_id,
        "status": "pending",
        "duplicate_match_count": len(duplicate_matches),
    }
