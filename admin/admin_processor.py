from __future__ import annotations

from typing import Any, Dict, Optional

from datetime import datetime

from pydantic import BaseModel

from pad.pad_processor import (
    get_combined_players_path,
    get_wizbucks_path,
    get_player_log_path,
    _load_json,
    _save_json,
    _append_player_log_entry,
    _ensure_list,  # type: ignore[attr-defined]
    load_managers_config,
)


class AdminPlayerUpdatePayload(BaseModel):
    """Admin-initiated player update coming from the web admin portal.

    This is intentionally generic so the website can send an arbitrary field
    patch for a single player, along with optional WizBucks adjustments and
    a human-readable log event.
    """

    season: int
    admin: str  # admin username / ID from Discord mapping
    upid: str
    changes: Dict[str, Any]
    log_event: str
    log_source: str = "admin_portal"
    update_type: str = "admin_manual"

    # Optional WizBucks adjustment to apply as part of the same transaction.
    wizbucks_team: Optional[str] = None  # FBP team abbreviation (e.g., "WIZ")
    wizbucks_delta: Optional[int] = None  # positive or negative delta


class AdminWBAdjustmentPayload(BaseModel):
    """Manual WizBucks adjustment coming from the admin portal.

    This is used by the WizBucks tab for PAD/KAP/APA/PTDA corrections and
    one-off admin actions. It intentionally mirrors the fields from the
    hub's admin form.
    """

    season: int
    admin: str
    team: str  # FBP team abbreviation (e.g., "WIZ")
    installment: str  # e.g. "pad", "kap", "apa", "ptda"
    amount: int  # positive for credit, negative for debit
    reason: str


def _resolve_franchise_name(team_abbr: str, wizbucks: Dict[str, int]) -> str:
    """Map FBP team abbreviation to the franchise name key in wizbucks.json.

    Mirrors the logic used in PAD processing so that the same franchise
    naming conventions are respected here.
    """

    managers_cfg = load_managers_config() or {}
    teams_meta = managers_cfg.get("teams") or {}

    meta = teams_meta.get(team_abbr)
    if isinstance(meta, dict):
        name = meta.get("name")
        if name and name in wizbucks:
            return name

    # Fallback: case-insensitive scan of wizbucks keys.
    upper_abbr = team_abbr.upper()
    for name in wizbucks.keys():
        if upper_abbr in name.upper() or name.upper().startswith(upper_abbr):
            return name

    return team_abbr


def apply_admin_wb_adjustment(
    payload: AdminWBAdjustmentPayload,
    test_mode: bool,
) -> Dict[str, Any]:
    """Apply a manual WizBucks adjustment and append to the WB ledger.

    This mutates wizbucks.json and wizbucks_transactions.json so that the
    website and Sheets-driven ledger stay in sync. The adjustment is made at
    the franchise-name level ("Hammers", "Whiz Kids", etc.) and is keyed off
    the FBP team abbreviation provided by the admin portal.
    """

    wizbucks_path = get_wizbucks_path(test_mode)
    wizbucks: Dict[str, int] = _load_json(wizbucks_path) or {}

    # Ledger currently has no test-mode variant; we always write to the main
    # wizbucks_transactions.json file.
    ledger_path = "data/wizbucks_transactions.json"
    ledger: list = _ensure_list(_load_json(ledger_path))

    franchise_name = _resolve_franchise_name(payload.team, wizbucks)
    balance_before = int(wizbucks.get(franchise_name, 0))
    balance_after = balance_before + int(payload.amount)
    wizbucks[franchise_name] = balance_after

    # Determine next ledger ID
    next_id = 1
    if ledger:
        try:
            next_id = max(int(rec.get("id", 0) or 0) for rec in ledger) + 1
        except Exception:
            next_id = len(ledger) + 1

    # Map amount to credit/debit columns used by the sheet-derived ledger
    credit = payload.amount if payload.amount > 0 else 0
    debit = -payload.amount if payload.amount < 0 else 0

    # Use a simple MM/DD/YYYY string for the ledger date to match the sheet.
    date_str = datetime.now().strftime("%m/%d/%Y")

    ledger_entry = {
        "id": next_id,
        "action": "Admin Portal",
        "note": payload.reason,
        "date": date_str,
        "credit": credit,
        "debit": debit,
        "manager": franchise_name,
        "balance": balance_after,
    }

    ledger.append(ledger_entry)

    _save_json(wizbucks_path, wizbucks)
    _save_json(ledger_path, ledger)

    return {
        "team": payload.team,
        "franchise_name": franchise_name,
        "amount": payload.amount,
        "installment": payload.installment,
        "reason": payload.reason,
        "balance_before": balance_before,
        "balance_after": balance_after,
    }


def apply_admin_player_update(
    payload: AdminPlayerUpdatePayload,
    test_mode: bool,
) -> Dict[str, Any]:
    """Apply a single admin player update to core JSON data files.

    Responsibilities:
      * Locate player in combined_players by UPID
      * Apply requested field changes
      * Optionally apply a WizBucks delta for a team
      * Append a snapshot-style entry to player_log
      * Persist combined_players, wizbucks, and player_log
    """

    combined_path = get_combined_players_path(test_mode)
    wizbucks_path = get_wizbucks_path(test_mode)
    player_log_path = get_player_log_path(test_mode)

    combined_players: list = _ensure_list(_load_json(combined_path))
    wizbucks: Dict[str, int] = _load_json(wizbucks_path) or {}
    player_log: list = _ensure_list(_load_json(player_log_path))

    # Locate player by UPID
    target_upid = str(payload.upid).strip()
    player = next(
        (p for p in combined_players if str(p.get("upid") or "").strip() == target_upid),
        None,
    )
    if not player:
        raise ValueError(f"Player with UPID {payload.upid} not found in combined_players")

    # Apply field changes
    for field, value in payload.changes.items():
        player[field] = value

    # Optional WizBucks adjustment as part of the same transaction
    new_wb_balance: Optional[int] = None
    if payload.wizbucks_delta is not None and payload.wizbucks_team:
        franchise_name = _resolve_franchise_name(payload.wizbucks_team, wizbucks)
        current_balance = int(wizbucks.get(franchise_name, 0))
        new_wb_balance = current_balance + int(payload.wizbucks_delta)
        wizbucks[franchise_name] = new_wb_balance

    # Append player_log snapshot entry
    _append_player_log_entry(
        player_log,
        player,
        season=payload.season,
        source=payload.log_source,
        update_type=payload.update_type,
        event=payload.log_event,
        admin=payload.admin,
    )

    # Persist files
    _save_json(combined_path, combined_players)
    _save_json(wizbucks_path, wizbucks)
    _save_json(player_log_path, player_log)

    return {
        "upid": target_upid,
        "player": player,
        "wizbucks_balance": new_wb_balance,
    }
