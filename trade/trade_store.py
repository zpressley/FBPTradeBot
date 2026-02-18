from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from buyin.buyin_service import apply_keeper_buyin_purchase
from trade.trade_models import (
    TradeSubmitPayload,
    TradeTransfer,
    TradeTransferDraftPick,
    TradeTransferPlayer,
    TradeTransferWizbucks,
)


TRADES_PATH = "data/trades.json"
COMBINED_PLAYERS_PATH = "data/combined_players.json"
WIZBUCKS_PATH = "data/wizbucks.json"
WIZBUCKS_TRANSACTIONS_PATH = "data/wizbucks_transactions.json"
DRAFT_ORDER_2026_PATH = "data/draft_order_2026.json"
MANAGERS_CONFIG_PATH = "config/managers.json"

_commit_fn = None


def set_commit_fn(fn) -> None:
    """Provide a best-effort commit/push function from the runtime (health.py)."""
    global _commit_fn
    _commit_fn = fn


def _maybe_commit(message: str, file_paths: Optional[list[str]] = None) -> None:
    if _commit_fn is None:
        return
    try:
        paths = file_paths or ["data/trades.json"]
        _commit_fn(paths, message)
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


def _load_draft_order_2026() -> list:
    draft_order = _load_json(DRAFT_ORDER_2026_PATH, []) or []
    return draft_order if isinstance(draft_order, list) else []


def _index_keeper_picks(draft_order: list) -> dict[tuple[int, int], tuple[int, dict]]:
    idx: dict[tuple[int, int], tuple[int, dict]] = {}
    for i, p in enumerate(draft_order):
        if not isinstance(p, dict) or p.get("draft") != "keeper":
            continue
        try:
            r = int(p.get("round") or 0)
            k = int(p.get("pick") or 0)
        except Exception:
            continue
        if r <= 0 or k <= 0:
            continue
        idx[(r, k)] = (i, p)
    return idx


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

    player_type = str(player.get("player_type") or "").strip().lower()

    # Prospects: display contract abbreviations instead of years_simple "P".
    if player_type == "farm":
        raw_ct = str(player.get("contract_type") or "Development Cont.").strip().lower()
        if "blue" in raw_ct and "chip" in raw_ct:
            contract = "BC"
        elif "purchased" in raw_ct:
            contract = "PC"
        else:
            # Default for Farm players without explicit contract_type.
            contract = "DC"
    else:
        contract = player.get("years_simple") or player.get("contract_type") or "?"

    return f"{pos} {name} [{mlb}] [{contract}]"


def _transfer_key(t: TradeTransfer) -> str:
    if isinstance(t, TradeTransferPlayer):
        return f"player:{t.upid}"
    if isinstance(t, TradeTransferDraftPick):
        return f"pick:{t.draft}:{t.round}:{t.pick}"
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

    has_pick_transfers = any(isinstance(t, TradeTransferDraftPick) for t in payload.transfers)
    if has_pick_transfers and window.window != "KAP":
        raise HTTPException(status_code=400, detail="Draft pick trades are only allowed during KAP")

    # validate transfers
    seen_players: set[str] = set()
    seen_picks: set[tuple[int, int]] = set()
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

        if isinstance(t, TradeTransferDraftPick):
            if str(t.draft or "").strip().lower() != "keeper":
                raise HTTPException(status_code=400, detail="Only keeper draft picks can be traded")
            if int(t.round) < 1 or int(t.round) > 15:
                raise HTTPException(status_code=400, detail="Draft pick round must be between 1 and 15")
            if int(t.pick) < 1:
                raise HTTPException(status_code=400, detail="Draft pick number must be >= 1")

            key = (int(t.round), int(t.pick))
            if key in seen_picks:
                raise HTTPException(status_code=400, detail="Same draft pick included multiple times")
            seen_picks.add(key)

        if isinstance(t, TradeTransferWizbucks):
            if t.amount <= 0:
                raise HTTPException(status_code=400, detail="Wizbucks amount must be positive")
            if t.amount % 5 != 0:
                raise HTTPException(status_code=400, detail="Wizbucks amount must be in $5 increments")


def _validate_rosters_and_wizbucks(payload: TradeSubmitPayload) -> None:
    players_by_upid = _load_players_by_upid()
    wizbucks = _load_json(WIZBUCKS_PATH, {}) or {}

    # validate draft pick ownership (keeper picks only)
    keeper_index: Optional[dict[tuple[int, int], tuple[int, dict]]] = None
    for t in payload.transfers:
        if not isinstance(t, TradeTransferDraftPick):
            continue

        if keeper_index is None:
            draft_order = _load_draft_order_2026()
            keeper_index = _index_keeper_picks(draft_order)

        key = (int(t.round), int(t.pick))
        if key not in keeper_index:
            raise HTTPException(status_code=400, detail=f"Keeper pick R{t.round} P{t.pick} not found")

        entry = keeper_index[key][1]
        current_owner = str(entry.get("current_owner") or "").strip().upper()
        if current_owner != t.from_team.strip().upper():
            raise HTTPException(
                status_code=400,
                detail=f"Keeper pick R{t.round} P{t.pick} is owned by {current_owner or 'UNOWNED'}, not {t.from_team}",
            )

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

    # keeper draft picks
    for t in payload.transfers:
        if isinstance(t, TradeTransferDraftPick):
            to_team = t.to_team.strip().upper()
            receives[to_team].append(f"Keeper Pick R{int(t.round)} P{int(t.pick)} via {t.from_team.strip().upper()}")

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


def _load_wizbucks_transactions() -> list[dict]:
    txns = _load_json(WIZBUCKS_TRANSACTIONS_PATH, [])
    return txns if isinstance(txns, list) else []


def _save_wizbucks_transactions(txns: list[dict]) -> None:
    _save_json(WIZBUCKS_TRANSACTIONS_PATH, txns)


def _apply_approved_trade_to_data_files(rec: dict, admin_team: str) -> list[str]:
    """Best-effort: apply player/WB ownership changes to JSON data files.

    Returns a list of warnings/errors (empty on success).

    Note: This mutates `data/combined_players.json`, `data/wizbucks.json`, and
    `data/wizbucks_transactions.json`. If upstream Yahoo rosters haven't been
    updated yet, the next data pipeline run may overwrite these ownership
    changes.
    """
    warnings: list[str] = []

    # Idempotency guard
    if rec.get("data_applied_at"):
        return warnings

    transfers = rec.get("transfers") or []
    if not isinstance(transfers, list) or not transfers:
        return warnings

    # Load data files
    players: list = _load_json(COMBINED_PLAYERS_PATH, []) or []
    if not isinstance(players, list):
        warnings.append("combined_players.json is not a list")
        return warnings

    players_by_upid: dict[str, dict] = {}
    for p in players:
        upid = str((p or {}).get("upid") or "").strip()
        if upid:
            players_by_upid[upid] = p

    wizbucks = _load_json(WIZBUCKS_PATH, {}) or {}
    if not isinstance(wizbucks, dict):
        warnings.append("wizbucks.json is not an object")
        wizbucks = {}

    txns = _load_wizbucks_transactions()

    now = _utc_now()
    trade_id = str(rec.get("trade_id") or "")

    has_pick_transfers = any(isinstance(t, dict) and t.get("type") == "draft_pick" for t in transfers)
    draft_order = _load_draft_order_2026() if has_pick_transfers else []
    keeper_index = _index_keeper_picks(draft_order) if has_pick_transfers else {}
    managers_data = _load_managers_config() if has_pick_transfers else {}

    # Apply player ownership changes
    moved_players = 0
    moved_picks = 0
    buyins_purchased = 0
    for t in transfers:
        if not isinstance(t, dict) or t.get("type") != "player":
            continue

        upid = str(t.get("upid") or "").strip()
        from_team = str(t.get("from_team") or "").strip().upper()
        to_team = str(t.get("to_team") or "").strip().upper()
        if not upid or not from_team or not to_team:
            continue

        p = players_by_upid.get(upid)
        if not p:
            warnings.append(f"UPID {upid} not found in combined_players.json")
            continue

        current_owner = str(p.get("FBP_Team") or "").strip().upper()
        if current_owner and current_owner != from_team:
            warnings.append(f"Player {p.get('name','')} ({upid}) owned by {current_owner}, expected {from_team}")
            continue

        p["FBP_Team"] = to_team
        # manager field uses franchise display name (matches wizbucks.json keys)
        p["manager"] = _resolve_franchise_name(to_team, wizbucks)
        moved_players += 1

    # Apply keeper draft pick transfers (ownership + traded flag)
    if has_pick_transfers and isinstance(keeper_index, dict):
        for t in transfers:
            if not isinstance(t, dict) or t.get("type") != "draft_pick":
                continue

            try:
                r = int(t.get("round") or 0)
                k = int(t.get("pick") or 0)
            except Exception:
                warnings.append("Invalid draft_pick transfer (round/pick not int)")
                continue

            if str(t.get("draft") or "keeper").strip().lower() != "keeper":
                warnings.append(f"Unsupported draft_pick draft type for R{r} P{k}")
                continue

            from_team = str(t.get("from_team") or "").strip().upper()
            to_team = str(t.get("to_team") or "").strip().upper()
            if r <= 0 or k <= 0 or not from_team or not to_team:
                continue

            key = (r, k)
            indexed = keeper_index.get(key)
            if not indexed:
                warnings.append(f"Keeper pick R{r} P{k} not found in draft_order_2026.json")
                continue

            pick_index, pick_entry = indexed
            current_owner = str(pick_entry.get("current_owner") or "").strip().upper()
            if current_owner != from_team:
                warnings.append(f"Keeper pick R{r} P{k} owned by {current_owner}, expected {from_team}")
                continue

            # Auto-buyin for rounds 1-3 (charge sending team) at admin approval time.
            if r in (1, 2, 3) and pick_entry.get("buyin_required") and not pick_entry.get("buyin_purchased"):
                try:
                    apply_keeper_buyin_purchase(
                        team=from_team,
                        round=r,
                        pick=k,
                        draft_order=draft_order,
                        managers_data=managers_data,
                        ledger=txns,
                        purchased_by=admin_team,
                        source="trade_portal_auto_buyin",
                        trade_id=trade_id or None,
                        now=now,
                    )
                    buyins_purchased += 1
                    # Refresh after in-memory mutation
                    pick_entry = draft_order[pick_index]
                except Exception as exc:
                    warnings.append(f"Auto-buyin failed for {from_team} R{r} P{k}: {exc}")
                    continue

            pick_entry["current_owner"] = to_team
            pick_entry["traded"] = True
            draft_order[pick_index] = pick_entry
            keeper_index[key] = (pick_index, pick_entry)
            moved_picks += 1

    # Apply WizBucks transfers + ledger
    moved_wb = 0
    for t in transfers:
        if not isinstance(t, dict) or t.get("type") != "wizbucks":
            continue

        try:
            amt = int(t.get("amount") or 0)
        except Exception:
            amt = 0

        from_team = str(t.get("from_team") or "").strip().upper()
        to_team = str(t.get("to_team") or "").strip().upper()
        if amt <= 0 or not from_team or not to_team:
            continue

        from_franchise = _resolve_franchise_name(from_team, wizbucks)
        to_franchise = _resolve_franchise_name(to_team, wizbucks)

        if from_franchise not in wizbucks or to_franchise not in wizbucks:
            warnings.append(f"Could not resolve WizBucks wallets for {from_team}->{to_team}")
            continue

        before_from = int(wizbucks.get(from_franchise, 0))
        before_to = int(wizbucks.get(to_franchise, 0))
        if before_from < amt:
            warnings.append(f"Insufficient WizBucks for {from_team}: have {before_from}, need {amt}")
            continue

        wizbucks[from_franchise] = before_from - amt
        wizbucks[to_franchise] = before_to + amt

        ts = _iso(now)
        base = f"wb_{now.strftime('%Y-%m-%d')}_trade_{trade_id}" if trade_id else f"wb_{now.strftime('%Y-%m-%d')}_trade"

        txns.append(
            {
                "txn_id": f"{base}_{from_team}_debit_{amt}",
                "timestamp": ts,
                "team": from_team,
                "amount": -amt,
                "balance_before": before_from,
                "balance_after": before_from - amt,
                "transaction_type": "trade_wizbucks_debit",
                "description": f"Trade {trade_id}: WizBucks sent to {to_team}",
                "related_player": None,
                "metadata": {"trade_id": trade_id, "counterparty": to_team, "source": "trade_portal"},
            }
        )
        txns.append(
            {
                "txn_id": f"{base}_{to_team}_credit_{amt}",
                "timestamp": ts,
                "team": to_team,
                "amount": amt,
                "balance_before": before_to,
                "balance_after": before_to + amt,
                "transaction_type": "trade_wizbucks_credit",
                "description": f"Trade {trade_id}: WizBucks received from {from_team}",
                "related_player": None,
                "metadata": {"trade_id": trade_id, "counterparty": from_team, "source": "trade_portal"},
            }
        )

        moved_wb += 1

    # Persist data file mutations
    if moved_players:
        _save_json(COMBINED_PLAYERS_PATH, players)

    if moved_wb:
        _save_json(WIZBUCKS_PATH, wizbucks)

    if moved_picks > 0 or buyins_purchased > 0:
        _save_json(DRAFT_ORDER_2026_PATH, draft_order)

    if buyins_purchased > 0:
        _save_json(MANAGERS_CONFIG_PATH, managers_data)

    if moved_wb > 0 or buyins_purchased > 0:
        _save_wizbucks_transactions(txns)

    rec["data_applied_at"] = _iso(now)
    rec["data_applied_by"] = admin_team
    rec["data_applied_summary"] = {
        "player_moves": moved_players,
        "pick_moves": moved_picks,
        "wb_transfers": moved_wb,
        "buyins_purchased": buyins_purchased,
        "warnings": warnings,
    }

    return warnings


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

    # Best-effort: apply ownership / WB changes into JSON files
    try:
        _apply_approved_trade_to_data_files(rec, admin_team)
    except Exception as exc:
        rec.setdefault("data_applied_summary", {})
        rec["data_applied_summary"]["warnings"] = (rec["data_applied_summary"].get("warnings") or []) + [
            f"Exception while applying data updates: {exc}"
        ]

    trades[trade_id] = rec
    _save_trades(trades)

    files_to_commit = ["data/trades.json"]

    summary = rec.get("data_applied_summary") if isinstance(rec.get("data_applied_summary"), dict) else {}
    moved_players = int(summary.get("player_moves") or 0)
    moved_picks = int(summary.get("pick_moves") or 0)
    moved_wb = int(summary.get("wb_transfers") or 0)
    buyins_purchased = int(summary.get("buyins_purchased") or 0)

    if moved_players > 0:
        files_to_commit.append(COMBINED_PLAYERS_PATH)

    if moved_picks > 0:
        files_to_commit.append(DRAFT_ORDER_2026_PATH)

    if moved_wb > 0:
        files_to_commit.extend([WIZBUCKS_PATH, WIZBUCKS_TRANSACTIONS_PATH])

    if buyins_purchased > 0:
        # Auto-buyin touches: draft_order_2026.json + managers.json + wizbucks_transactions.json
        if DRAFT_ORDER_2026_PATH not in files_to_commit:
            files_to_commit.append(DRAFT_ORDER_2026_PATH)
        files_to_commit.extend([MANAGERS_CONFIG_PATH, WIZBUCKS_TRANSACTIONS_PATH])

    # de-dupe while preserving order
    seen = set()
    files_to_commit = [p for p in files_to_commit if not (p in seen or seen.add(p))]

    _maybe_commit(f"Trade admin approve: {trade_id} by {admin_team}", file_paths=files_to_commit)
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
