from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from trade.trade_models import TradeSubmitPayload, TradeTransfer, TradeTransferPlayer, TradeTransferWizbucks


TRADES_PATH = "data/trades.json"
COMBINED_PLAYERS_PATH = "data/combined_players.json"
WIZBUCKS_PATH = "data/wizbucks.json"
MANAGERS_CONFIG_PATH = "config/managers.json"

_commit_fn = None


def set_commit_fn(fn) -> None:
    """Provide a best-effort commit/push function from the runtime (health.py)."""
    global _commit_fn
    _commit_fn = fn


def _maybe_commit(message: str) -> None:
    if _commit_fn is None:
        return
    try:
        _commit_fn(["data/trades.json"], message)
    except Exception as exc:
        print(f"⚠️ Trade commit/push skipped: {exc}")


@dataclass(frozen=True)
class TradeWindowStatus:
    is_open: bool
    window: str
    detail: str


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    # Keep explicit Z suffix for stable JSON
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_date_yyyy_mm_dd(s: str) -> datetime:
    # interpret date-only in ET-ish as start-of-day UTC for comparisons. We only
    # need coarse window gating.
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def load_trade_window_status(now: Optional[datetime] = None) -> TradeWindowStatus:
    now = now or _utc_now()
    season_dates = _load_json("config/season_dates.json", {}) or {}

    season_year = int(season_dates.get("season_year") or now.year)
    kap_open = season_dates.get("kap_open_date")
    preseason_deadline = season_dates.get("preseason-trade deadline")
    auction_start = ((season_dates.get("auction") or {}) or {}).get("start")

    # July 31 (in-season deadline)
    july_31 = datetime(season_year, 7, 31, tzinfo=timezone.utc)

    def in_range(start_s: Optional[str], end_s: Optional[str]) -> bool:
        if not start_s or not end_s:
            return False
        try:
            start = _parse_date_yyyy_mm_dd(start_s)
            end = _parse_date_yyyy_mm_dd(end_s) + timedelta(days=1)
            return start <= now < end
        except Exception:
            return False

    if in_range(kap_open, preseason_deadline):
        return TradeWindowStatus(True, "KAP", f"Open (KAP) until {preseason_deadline}")

    if auction_start and _parse_date_yyyy_mm_dd(auction_start) <= now < july_31 + timedelta(days=1):
        return TradeWindowStatus(True, "IN_SEASON", f"Open (in-season) until {season_year}-07-31")

    return TradeWindowStatus(False, "CLOSED", "Trade window is closed")


def _load_players_by_upid() -> Dict[str, dict]:
    players: list = _load_json(COMBINED_PLAYERS_PATH, []) or []
    by_upid: Dict[str, dict] = {}
    for p in players:
        upid = str(p.get("upid") or "").strip()
        if upid:
            by_upid[upid] = p
    return by_upid


def _load_managers_config() -> dict:
    return _load_json(MANAGERS_CONFIG_PATH, {}) or {}


def _resolve_franchise_name(team_abbr: str, wizbucks: Dict[str, int]) -> str:
    cfg = _load_managers_config()
    meta = (cfg.get("teams") or {}).get(team_abbr)
    if isinstance(meta, dict):
        name = meta.get("name")
        if name and name in wizbucks:
            return name

    # fallback scan
    upper_abbr = team_abbr.upper()
    for name in wizbucks.keys():
        if upper_abbr in name.upper() or name.upper().startswith(upper_abbr):
            return name

    return team_abbr


def _format_player_display(player: dict) -> str:
    pos = player.get("position") or "?"
    name = player.get("name") or "Unknown"
    mlb = player.get("team") or "FA"
    contract = player.get("years_simple") or player.get("contract_type") or "?"
    return f"{pos} {name} [{mlb}] [{contract}]"


def _transfer_key(t: TradeTransfer) -> str:
    if isinstance(t, TradeTransferPlayer):
        return f"player:{t.upid}"
    if isinstance(t, TradeTransferWizbucks):
        return f"wb:{t.from_team}->{t.to_team}:{t.amount}"
    return "unknown"


def _validate_payload(payload: TradeSubmitPayload, actor_team: str) -> None:
    teams = [t.strip().upper() for t in payload.teams]
    if len(set(teams)) != len(teams):
        raise HTTPException(status_code=400, detail="Duplicate team in teams list")

    if actor_team.upper() not in teams:
        raise HTTPException(status_code=403, detail="You must be part of the trade to submit it")

    if len(teams) not in (2, 3):
        raise HTTPException(status_code=400, detail="Trade must have 2 or 3 teams")

    # validate window
    window = load_trade_window_status()
    if not window.is_open:
        raise HTTPException(status_code=400, detail=window.detail)

    # validate transfers
    seen_players: set[str] = set()
    seen_transfer_keys: set[str] = set()

    for t in payload.transfers:
        if t.from_team.strip().upper() not in teams:
            raise HTTPException(status_code=400, detail=f"from_team {t.from_team} not in teams")
        if t.to_team.strip().upper() not in teams:
            raise HTTPException(status_code=400, detail=f"to_team {t.to_team} not in teams")
        if t.from_team.strip().upper() == t.to_team.strip().upper():
            raise HTTPException(status_code=400, detail="Transfer from_team cannot equal to_team")

        key = _transfer_key(t)
        if key in seen_transfer_keys:
            raise HTTPException(status_code=400, detail="Duplicate transfer in payload")
        seen_transfer_keys.add(key)

        if isinstance(t, TradeTransferPlayer):
            upid = str(t.upid).strip()
            if not upid:
                raise HTTPException(status_code=400, detail="Missing player UPID")
            if upid in seen_players:
                raise HTTPException(status_code=400, detail="Same player included multiple times")
            seen_players.add(upid)

        if isinstance(t, TradeTransferWizbucks):
            if t.amount <= 0:
                raise HTTPException(status_code=400, detail="Wizbucks amount must be positive")
            if t.amount % 5 != 0:
                raise HTTPException(status_code=400, detail="Wizbucks amount must be in $5 increments")


def _validate_rosters_and_wizbucks(payload: TradeSubmitPayload) -> None:
    players_by_upid = _load_players_by_upid()
    wizbucks = _load_json(WIZBUCKS_PATH, {}) or {}

    # validate player ownership
    for t in payload.transfers:
        if isinstance(t, TradeTransferPlayer):
            p = players_by_upid.get(str(t.upid).strip())
            if not p:
                raise HTTPException(status_code=400, detail=f"Player UPID {t.upid} not found")

            owner = str(p.get("FBP_Team") or "").strip().upper()
            if owner != t.from_team.strip().upper():
                raise HTTPException(
                    status_code=400,
                    detail=f"Player {p.get('name','')} is owned by {owner or 'UNOWNED'}, not {t.from_team}",
                )

    # validate wizbucks balances (proposal-time validation only)
    wb_out_by_team: Dict[str, int] = {}
    for t in payload.transfers:
        if isinstance(t, TradeTransferWizbucks):
            from_team = t.from_team.strip().upper()
            wb_out_by_team[from_team] = wb_out_by_team.get(from_team, 0) + int(t.amount)

    for team_abbr, wb_out in wb_out_by_team.items():
        franchise = _resolve_franchise_name(team_abbr, wizbucks)
        if franchise not in wizbucks:
            raise HTTPException(status_code=400, detail=f"Could not resolve WizBucks wallet for {team_abbr}")
        if int(wizbucks.get(franchise, 0)) < wb_out:
            raise HTTPException(status_code=400, detail=f"{team_abbr} has insufficient WizBucks for proposed send")


def _build_receives(payload: TradeSubmitPayload) -> Dict[str, List[str]]:
    players_by_upid = _load_players_by_upid()
    receives: Dict[str, List[str]] = {t.strip().upper(): [] for t in payload.teams}

    # players
    for t in payload.transfers:
        if isinstance(t, TradeTransferPlayer):
            to_team = t.to_team.strip().upper()
            p = players_by_upid.get(str(t.upid).strip())
            if not p:
                continue
            receives[to_team].append(_format_player_display(p))

    # wizbucks
    for t in payload.transfers:
        if isinstance(t, TradeTransferWizbucks):
            to_team = t.to_team.strip().upper()
            receives[to_team].append(f"${int(t.amount)} WB via {t.from_team.strip().upper()}")

    # stable ordering: players first then WB already appended after players
    return receives


def _load_trades() -> Dict[str, dict]:
    return _load_json(TRADES_PATH, {}) or {}


def _save_trades(trades: Dict[str, dict]) -> None:
    _save_json(TRADES_PATH, trades)


def _active_trade_count_for_initiator(trades: Dict[str, dict], initiator_team: str, now: datetime) -> int:
    count = 0
    for t in trades.values():
        if str(t.get("initiator_team") or "").upper() != initiator_team.upper():
            continue
        status = str(t.get("status") or "")
        if status not in ("pending", "partial_accept", "admin_review"):
            continue
        expires_at = str(t.get("expires_at") or "")
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except Exception:
            continue
        if exp > now:
            count += 1
    return count


def create_trade(payload: TradeSubmitPayload, actor_team: str) -> dict:
    actor_team = actor_team.strip().upper()
    payload = TradeSubmitPayload.model_validate(payload.model_dump())

    _validate_payload(payload, actor_team)
    _validate_rosters_and_wizbucks(payload)

    trades = _load_trades()
    now = _utc_now()

    initiator_team = actor_team
    if _active_trade_count_for_initiator(trades, initiator_team, now) >= 12:
        raise HTTPException(status_code=400, detail="Queue limit reached (12 max active trades)")

    trade_id = f"WEB-TRADE-{now.strftime('%Y%m%d-%H%M%S')}"
    expires_at = now + timedelta(days=14)

    receives = _build_receives(payload)

    record = {
        "trade_id": trade_id,
        "teams": [t.strip().upper() for t in payload.teams],
        "initiator_team": initiator_team,
        "status": "pending",
        "created_at": _iso(now),
        "expires_at": _iso(expires_at),
        "transfers": [t.model_dump() for t in payload.transfers],
        "acceptances": [initiator_team],
        "receives": receives,
        "discord": {
            "thread_id": None,
            "thread_url": None,
            "admin_review_message_id": None,
        },
    }

    trades[trade_id] = record
    _save_trades(trades)
    _maybe_commit(f"Trade submitted: {trade_id} by {initiator_team}")
    return record


def attach_discord_thread(trade_id: str, thread_id: str, thread_url: str) -> dict:
    trades = _load_trades()
    rec = trades.get(trade_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Trade not found")

    rec.setdefault("discord", {})
    rec["discord"]["thread_id"] = str(thread_id)
    rec["discord"]["thread_url"] = str(thread_url)
    trades[trade_id] = rec
    _save_trades(trades)
    _maybe_commit(f"Trade thread attached: {trade_id}")
    return rec


def get_trade(trade_id: str) -> dict:
    trades = _load_trades()
    rec = trades.get(trade_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Trade not found")
    return rec


def list_queue(team: str) -> List[dict]:
    team = team.strip().upper()
    trades = _load_trades()
    now = _utc_now()

    out: List[dict] = []
    for rec in trades.values():
        if str(rec.get("initiator_team") or "").upper() != team:
            continue
        # show even if expired? hide expired
        expires_at = str(rec.get("expires_at") or "")
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp <= now:
                continue
        except Exception:
            continue

        status = str(rec.get("status") or "")
        if status in ("pending", "partial_accept", "admin_review"):
            out.append(rec)

    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out


def list_inbox(team: str) -> List[dict]:
    team = team.strip().upper()
    trades = _load_trades()
    now = _utc_now()

    out: List[dict] = []
    for rec in trades.values():
        teams = [str(t).upper() for t in rec.get("teams") or []]
        if team not in teams:
            continue
        if str(rec.get("initiator_team") or "").upper() == team:
            continue

        expires_at = str(rec.get("expires_at") or "")
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp <= now:
                continue
        except Exception:
            continue

        status = str(rec.get("status") or "")
        if status in ("pending", "partial_accept"):
            out.append(rec)

    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out


def list_history(team: str) -> List[dict]:
    team = team.strip().upper()
    trades = _load_trades()

    out: List[dict] = []
    for rec in trades.values():
        teams = [str(t).upper() for t in rec.get("teams") or []]
        if team not in teams:
            continue
        status = str(rec.get("status") or "")
        if status in ("approved", "rejected", "withdrawn", "admin_rejected"):
            out.append(rec)

    out.sort(key=lambda r: r.get("processed_at") or r.get("created_at") or "", reverse=True)
    return out[:50]


def accept_trade(trade_id: str, team: str) -> Tuple[dict, bool]:
    team = team.strip().upper()
    trades = _load_trades()
    rec = trades.get(trade_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Trade not found")

    teams = [str(t).upper() for t in rec.get("teams") or []]
    if team not in teams:
        raise HTTPException(status_code=403, detail="Not part of this trade")

    status = str(rec.get("status") or "")
    if status not in ("pending", "partial_accept"):
        raise HTTPException(status_code=400, detail=f"Trade is not accept-able in status {status}")

    acceptances: list = rec.get("acceptances") or []
    acceptances = [str(t).upper() for t in acceptances]
    if team not in acceptances:
        acceptances.append(team)

    rec["acceptances"] = acceptances

    all_accepted = all(t in acceptances for t in teams)
    if all_accepted:
        rec["status"] = "admin_review"
        rec["manager_approved_at"] = _iso(_utc_now())
    else:
        rec["status"] = "partial_accept"

    trades[trade_id] = rec
    _save_trades(trades)
    _maybe_commit(f"Trade accept: {trade_id} by {team}")
    return rec, all_accepted


def reject_trade(trade_id: str, team: str, reason: str) -> dict:
    team = team.strip().upper()
    trades = _load_trades()
    rec = trades.get(trade_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Trade not found")

    teams = [str(t).upper() for t in rec.get("teams") or []]
    if team not in teams:
        raise HTTPException(status_code=403, detail="Not part of this trade")

    status = str(rec.get("status") or "")
    if status in ("approved", "withdrawn", "rejected", "admin_rejected"):
        raise HTTPException(status_code=400, detail=f"Trade already finalized with status {status}")

    rec["status"] = "rejected"
    rec["rejection_reason"] = reason
    rec["rejected_by"] = team
    rec["processed_at"] = _iso(_utc_now())

    trades[trade_id] = rec
    _save_trades(trades)
    _maybe_commit(f"Trade reject: {trade_id} by {team}")
    return rec


def withdraw_trade(trade_id: str, team: str) -> dict:
    team = team.strip().upper()
    trades = _load_trades()
    rec = trades.get(trade_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Trade not found")

    if str(rec.get("initiator_team") or "").upper() != team:
        raise HTTPException(status_code=403, detail="Only the initiating team can withdraw")

    status = str(rec.get("status") or "")
    if status not in ("pending", "partial_accept"):
        raise HTTPException(status_code=400, detail=f"Trade is not withdrawable in status {status}")

    rec["status"] = "withdrawn"
    rec["processed_at"] = _iso(_utc_now())

    trades[trade_id] = rec
    _save_trades(trades)
    _maybe_commit(f"Trade withdraw: {trade_id} by {team}")
    return rec


def admin_approve(trade_id: str, admin_team: str) -> dict:
    admin_team = admin_team.strip().upper()
    trades = _load_trades()
    rec = trades.get(trade_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Trade not found")

    if str(rec.get("status") or "") != "admin_review":
        raise HTTPException(status_code=400, detail="Trade not in admin_review")

    rec["status"] = "approved"
    rec["admin_decision_by"] = admin_team
    rec["processed_at"] = _iso(_utc_now())

    trades[trade_id] = rec
    _save_trades(trades)
    _maybe_commit(f"Trade admin approve: {trade_id} by {admin_team}")
    return rec


def admin_reject(trade_id: str, admin_team: str, reason: str) -> dict:
    admin_team = admin_team.strip().upper()
    trades = _load_trades()
    rec = trades.get(trade_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Trade not found")

    if str(rec.get("status") or "") != "admin_review":
        raise HTTPException(status_code=400, detail="Trade not in admin_review")

    rec["status"] = "admin_rejected"
    rec["admin_decision_by"] = admin_team
    rec["rejection_reason"] = reason
    rec["processed_at"] = _iso(_utc_now())

    trades[trade_id] = rec
    _save_trades(trades)
    _maybe_commit(f"Trade admin reject: {trade_id} by {admin_team}")
    return rec
