from __future__ import annotations

from typing import Any, Dict, Optional

from datetime import datetime, timezone

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

    Used for one-off admin WizBucks adjustments. Installment is optional
    since adjustments come from the main wallet.
    """

    season: int
    admin: str
    team: str  # FBP team abbreviation (e.g., "WIZ")
    amount: int  # positive for credit, negative for debit
    reason: str
    installment: Optional[str] = "admin"  # defaults to "admin" for manual adjustments


class AdminDeletePlayerPayload(BaseModel):
    """Admin-initiated player deletion from the web admin portal.
    
    Used for removing duplicate records or retired players from the database.
    """
    
    upid: str
    admin: str  # admin username / ID
    reason: str  # required reason for deletion


class AdminMergePlayersPayload(BaseModel):
    """Admin-initiated player merge from the web admin portal.
    
    Merges source player data into target player, then deletes source.
    Fields from source are only used to fill in missing/empty target fields.
    """
    
    source_upid: str  # player to be deleted after merge
    target_upid: str  # player to keep and receive merged data
    admin: str  # admin username / ID


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
    # wizbucks_transactions.json file using the 2026+ schema shared with PAD
    # (txn_id / timestamp / team / amount / balance_* / transaction_type).
    ledger_path = "data/wizbucks_transactions.json"
    ledger: list = _ensure_list(_load_json(ledger_path))

    franchise_name = _resolve_franchise_name(payload.team, wizbucks)
    balance_before = int(wizbucks.get(franchise_name, 0))
    balance_after = balance_before + int(payload.amount)
    wizbucks[franchise_name] = balance_after

    # New-style ledger entry keyed by team abbreviation and amount delta.
    # Positive amounts are credits; negative amounts are debits.
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    txn_id = f"wb_{payload.season}_ADMIN_{payload.team}_{int(datetime.now().timestamp())}"

    ledger_entry = {
        "txn_id": txn_id,
        "timestamp": now_iso,
        "team": payload.team,
        "amount": int(payload.amount),
        "balance_before": balance_before,
        "balance_after": balance_after,
        "transaction_type": "admin_adjustment",
        "description": payload.reason,
        "related_player": None,
        "metadata": {
            "season": payload.season,
            "installment": payload.installment,
            "admin": payload.admin,
            "source": "admin_portal",
        },
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


def apply_admin_delete_player(
    payload: AdminDeletePlayerPayload,
    test_mode: bool,
) -> Dict[str, Any]:
    """Delete a player from combined_players.json.
    
    This permanently removes the player record. The deletion is logged
    to player_log for audit purposes.
    """
    
    combined_path = get_combined_players_path(test_mode)
    player_log_path = get_player_log_path(test_mode)
    
    combined_players: list = _ensure_list(_load_json(combined_path))
    player_log: list = _ensure_list(_load_json(player_log_path))
    
    # Locate player by UPID
    target_upid = str(payload.upid).strip()
    player = next(
        (p for p in combined_players if str(p.get("upid") or "").strip() == target_upid),
        None,
    )
    if not player:
        raise ValueError(f"Player with UPID {payload.upid} not found in combined_players")
    
    # Store player info for log before deletion
    player_name = player.get("name", "Unknown")
    player_team = player.get("team", "")
    player_manager = player.get("manager", "")
    
    # Remove player from list
    combined_players = [p for p in combined_players if str(p.get("upid") or "").strip() != target_upid]
    
    # Log the deletion
    now_iso = datetime.now().isoformat()
    season = datetime.now().year
    
    log_entry = {
        "id": f"{season}-{now_iso}-UPID_{target_upid}-Delete-Admin_Portal",
        "season": season,
        "source": "Admin Portal",
        "admin": payload.admin,
        "timestamp": now_iso,
        "upid": target_upid,
        "player_name": player_name,
        "team": player_team,
        "owner": player_manager,
        "update_type": "Delete",
        "event": f"DELETED: {payload.reason}",
        "changes": {"deleted": {"from": "exists", "to": "deleted"}},
    }
    player_log.append(log_entry)
    
    # Persist files
    _save_json(combined_path, combined_players)
    _save_json(player_log_path, player_log)
    
    return {
        "upid": target_upid,
        "player": {"name": player_name, "team": player_team, "manager": player_manager},
        "deleted": True,
    }


def apply_admin_merge_players(
    payload: AdminMergePlayersPayload,
    test_mode: bool,
) -> Dict[str, Any]:
    """Merge two player records, keeping the target and deleting the source.
    
    Fields from source are copied to target ONLY where target is missing/empty.
    The source player is then deleted. Both operations are logged.
    """
    
    combined_path = get_combined_players_path(test_mode)
    player_log_path = get_player_log_path(test_mode)
    
    combined_players: list = _ensure_list(_load_json(combined_path))
    player_log: list = _ensure_list(_load_json(player_log_path))
    
    # Locate both players
    source_upid = str(payload.source_upid).strip()
    target_upid = str(payload.target_upid).strip()
    
    source_player = next(
        (p for p in combined_players if str(p.get("upid") or "").strip() == source_upid),
        None,
    )
    target_player = next(
        (p for p in combined_players if str(p.get("upid") or "").strip() == target_upid),
        None,
    )
    
    if not source_player:
        raise ValueError(f"Source player with UPID {source_upid} not found")
    if not target_player:
        raise ValueError(f"Target player with UPID {target_upid} not found")
    if source_upid == target_upid:
        raise ValueError("Cannot merge a player with itself")
    
    # Track what gets merged
    merged_fields = {}
    
    # Fields to consider for merging (fill missing target values from source)
    mergeable_fields = [
        "team", "position", "manager", "player_type", "contract_type", "status",
        "years_simple", "yahoo_id", "mlb_id", "FBP_Team", "birth_date", "age",
        "height", "weight", "bats", "throws", "mlb_primary_position", "fypd"
    ]
    
    for field in mergeable_fields:
        source_val = source_player.get(field)
        target_val = target_player.get(field)
        
        # Check if target is missing/empty and source has a value
        target_empty = target_val is None or target_val == "" or target_val == []
        source_has = source_val is not None and source_val != "" and source_val != []
        
        if target_empty and source_has:
            target_player[field] = source_val
            merged_fields[field] = {"from": source_val}
    
    # Remove source from combined_players
    combined_players = [p for p in combined_players if str(p.get("upid") or "").strip() != source_upid]
    
    # Log the merge
    now_iso = datetime.now().isoformat()
    season = datetime.now().year
    
    log_entry = {
        "id": f"{season}-{now_iso}-UPID_{target_upid}-Merge-Admin_Portal",
        "season": season,
        "source": "Admin Portal",
        "admin": payload.admin,
        "timestamp": now_iso,
        "upid": target_upid,
        "player_name": target_player.get("name", "Unknown"),
        "team": target_player.get("team", ""),
        "owner": target_player.get("manager", ""),
        "update_type": "Merge",
        "event": f"MERGED: {source_player.get('name', 'Unknown')} (UPID {source_upid}) merged into this record",
        "changes": merged_fields,
        "merged_from": {
            "upid": source_upid,
            "name": source_player.get("name", "Unknown"),
        },
    }
    player_log.append(log_entry)
    
    # Persist files
    _save_json(combined_path, combined_players)
    _save_json(player_log_path, player_log)
    
    return {
        "source_upid": source_upid,
        "target_upid": target_upid,
        "player": target_player,
        "merged_fields": merged_fields,
        "source_deleted": True,
    }
