from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel

from data_lock import DATA_LOCK
from pad.pad_processor import (
    ET,
    _append_player_log_entry,
    _ensure_list,
    _load_json,
    _save_json,
    get_combined_players_path,
    get_player_log_path,
    get_wizbucks_path,
    load_managers_config,
)


class ContractPurchasePayload(BaseModel):
    season: int
    team: str  # FBP team abbreviation (e.g., "CFL")
    upid: str

    # Desired *final* contract type.
    new_contract_type: str  # "Purchased Contract" | "Blue Chip Contract"

    # Used for player_log `source` and ledger metadata.
    log_source: str = "Dashboard Self-Service"


@dataclass(frozen=True)
class _ContractState:
    code: str  # DC | PC | BC
    canonical: str  # Development Cont. | Purchased Contract | Blue Chip Contract


def _normalize_contract(contract_type: str | None) -> Optional[_ContractState]:
    if not contract_type:
        return None

    s = str(contract_type).strip().lower()
    if not s:
        return None

    if "development" in s:
        return _ContractState(code="DC", canonical="Development Cont.")
    if "purchased" in s:
        return _ContractState(code="PC", canonical="Purchased Contract")
    if "blue chip" in s or "bluechip" in s:
        return _ContractState(code="BC", canonical="Blue Chip Contract")

    return None


def _resolve_franchise_name(team_abbr: str, wizbucks: Dict[str, int]) -> str:
    """Map team abbreviation to the franchise key used in wizbucks.json."""

    managers_cfg = load_managers_config() or {}
    meta = (managers_cfg.get("teams") or {}).get(team_abbr)
    if isinstance(meta, dict):
        name = meta.get("name")
        if name and name in wizbucks:
            return name

    # Fallback: case-insensitive scan.
    upper_abbr = team_abbr.upper()
    for name in wizbucks.keys():
        if upper_abbr in name.upper() or name.upper().startswith(upper_abbr):
            return name

    return team_abbr


def _upgrade_cost(from_code: str, to_code: str) -> Optional[int]:
    costs = {
        ("DC", "PC"): 5,
        ("DC", "BC"): 15,
        ("PC", "BC"): 10,
    }
    return costs.get((from_code, to_code))


def _ownership_matches(team: str, franchise_name: str, player: dict) -> bool:
    """Return True if the player appears to be owned by the given team."""

    # Canonical in hub is FBP_Team.
    if str(player.get("FBP_Team") or "").strip().upper() == team.strip().upper():
        return True

    # Fallback: older records sometimes only have `manager`.
    manager = str(player.get("manager") or "").strip()
    if manager and manager.strip().lower() == str(franchise_name or "").strip().lower():
        return True

    return False


def apply_contract_purchase(payload: ContractPurchasePayload, test_mode: bool) -> Dict[str, Any]:
    """Apply a manager self-service contract upgrade.

    Mutates:
      * data/combined_players.json (contract_type only)
      * data/wizbucks.json (deduct cost)
      * data/wizbucks_transactions.json (append ledger entry)
      * data/player_log.json (append snapshot)
    """

    with DATA_LOCK:
        combined_path = get_combined_players_path(test_mode)
        wizbucks_path = get_wizbucks_path(test_mode)
        player_log_path = get_player_log_path(test_mode)

        combined_players: list = _ensure_list(_load_json(combined_path))
        wizbucks: Dict[str, int] = _load_json(wizbucks_path) or {}
        player_log: list = _ensure_list(_load_json(player_log_path))

        target_upid = str(payload.upid).strip()
        team = str(payload.team).strip().upper()
        if not team:
            raise ValueError("Missing team")

        player = next(
            (p for p in combined_players if str(p.get("upid") or "").strip() == target_upid),
            None,
        )
        if not player:
            raise ValueError(f"Player with UPID {payload.upid} not found in combined_players")

        if str(player.get("player_type") or "").strip() != "Farm":
            raise ValueError("Contract purchases are only allowed for Farm players")

        franchise_name = _resolve_franchise_name(team, wizbucks)
        if franchise_name not in wizbucks:
            # Avoid accidentally creating a new key and drifting the real wallet.
            raise ValueError(
                f"Could not resolve WizBucks wallet for team {team}. "
                "Check wizbucks.json keys and config/managers.json team name mapping.",
            )

        if not _ownership_matches(team, franchise_name, player):
            raise ValueError("You can only purchase contracts for players on your roster")

        from_state = _normalize_contract(player.get("contract_type"))
        to_state = _normalize_contract(payload.new_contract_type)

        if not from_state:
            raise ValueError("Player does not have an eligible current contract (must be DC or PC)")
        if from_state.code == "BC":
            raise ValueError("Player is already on a Blue Chip Contract")

        if not to_state or to_state.code not in ("PC", "BC"):
            raise ValueError("New contract type must be Purchased Contract (PC) or Blue Chip Contract (BC)")

        if from_state.code == to_state.code:
            raise ValueError("Player is already on that contract type")

        cost = _upgrade_cost(from_state.code, to_state.code)
        if cost is None:
            raise ValueError(f"Invalid upgrade: {from_state.code} -> {to_state.code}")

        balance_before = int(wizbucks.get(franchise_name, 0))
        if balance_before < cost:
            raise ValueError("Insufficient WizBucks")

        # Apply contract update (contract_type only).
        player["contract_type"] = to_state.canonical

        # Deduct WB.
        balance_after = balance_before - cost
        wizbucks[franchise_name] = balance_after

        # Ledger entry.
        now = datetime.now(tz=ET).isoformat()
        ledger_path = "data/wizbucks_transactions.json"
        ledger: list = _ensure_list(_load_json(ledger_path))

        upgrade = f"{from_state.code} → {to_state.code}"
        txn_id = f"wb_{payload.season}_CONTRACT_PURCHASE_{team}_{target_upid}_{int(datetime.now(tz=ET).timestamp())}"
        ledger_entry = {
            "txn_id": txn_id,
            "timestamp": now,
            "team": team,
            "amount": -cost,
            "balance_before": balance_before,
            "balance_after": balance_after,
            "transaction_type": "contract_purchase",
            "description": f"{payload.log_source}: {player.get('name', '')} {upgrade}",
            "related_player": {
                "upid": target_upid,
                "name": player.get("name") or "",
            },
            "metadata": {
                "season": payload.season,
                "source": payload.log_source,
                "upgrade": upgrade,
                "from_contract": from_state.canonical,
                "to_contract": to_state.canonical,
            },
        }
        ledger.append(ledger_entry)

        # Player log snapshot.
        event = f"{payload.season} {upgrade}"
        _append_player_log_entry(
            player_log,
            player,
            season=payload.season,
            source=payload.log_source,
            update_type="Purchase",
            event=event,
            admin=team,
        )

        _save_json(combined_path, combined_players)
        _save_json(wizbucks_path, wizbucks)
        _save_json(player_log_path, player_log)
        _save_json(ledger_path, ledger)

    return {
        "upid": target_upid,
        "team": team,
        "cost": cost,
        "player": player,
        "wizbucks_balance": balance_after,
        "ledger_entry": ledger_entry,
    }
