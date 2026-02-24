from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from team_utils import normalize_team_abbr
from wb_ledger import append_transaction as _wb_append, get_balance as _wb_balance


BUY_IN_COSTS: dict[int, int] = {
    1: 55,
    2: 35,
    3: 10,
}


@dataclass(frozen=True)
class BuyinApplyResult:
    team: str
    round: int
    pick: int
    cost: int
    wallet_balance_before: int
    wallet_balance_after: int
    ledger_entry: dict[str, Any]


@dataclass(frozen=True)
class BuyinRefundResult:
    team: str
    round: int
    pick: int
    amount: int
    wallet_balance_before: int
    wallet_balance_after: int
    ledger_entry: dict[str, Any]


def _now_iso(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def get_team_full_name(team_abbr: str, managers_data: dict) -> str:
    """Get team full name from abbreviation for wallet lookup.
    
    CRITICAL: wizbucks.json uses FULL NAMES not abbreviations!
    Example: "B2J" -> "Btwn2Jackies"
    """
    team_abbr = normalize_team_abbr(team_abbr, managers_data=managers_data)
    team_data = (managers_data.get("teams") or {}).get(team_abbr)
    if not isinstance(team_data, dict):
        raise ValueError(f"Team {team_abbr} not found in managers.json")
    
    full_name = team_data.get("full_name") or team_data.get("name")
    if not full_name:
        raise ValueError(f"Team {team_abbr} missing full_name in managers.json")
    
    return full_name


def get_wallet_balance(team: str, wizbucks_data: dict, managers_data: dict) -> int:
    """Get team's WizBucks wallet balance from wizbucks.json.
    
    CRITICAL: This is the SOURCE OF TRUTH for wallet balances.
    NOT managers.json, NOT any other file.
    """
    full_name = get_team_full_name(team, managers_data)
    
    balance = wizbucks_data.get(full_name)
    if balance is None:
        raise ValueError(f"Team {full_name} not found in wizbucks.json")
    
    try:
        return int(balance)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid balance for {full_name}: {balance}")


def update_wallet_balance(team: str, delta: int, wizbucks_data: dict, managers_data: dict) -> None:
    """Update team's WizBucks wallet balance in wizbucks.json.
    
    CRITICAL: This MUTATES wizbucks_data dict in place.
    Caller MUST save wizbucks.json after calling this.
    """
    full_name = get_team_full_name(team, managers_data)
    
    if full_name not in wizbucks_data:
        raise ValueError(f"Team {full_name} not found in wizbucks.json")
    
    try:
        current_balance = int(wizbucks_data[full_name])
    except (TypeError, ValueError):
        raise ValueError(f"Invalid current balance for {full_name}: {wizbucks_data[full_name]}")
    
    new_balance = current_balance + int(delta)
    if new_balance < 0:
        raise ValueError(f"Balance cannot go negative for {full_name}: {current_balance} + {delta} = {new_balance}")
    
    wizbucks_data[full_name] = new_balance


def apply_keeper_buyin_purchase(
    *,
    team: str,
    round: int,
    pick: Optional[int],
    draft_order: list[dict],
    managers_data: dict,
    purchased_by: str,
    source: str,
    trade_id: Optional[str] = None,
    season: int = 2026,
    now: Optional[datetime] = None,
    # Deprecated — ignored.  Kept for call-site compat during migration.
    wizbucks_data: dict | None = None,
    ledger: list[dict] | None = None,
) -> BuyinApplyResult:
    """Purchase a keeper draft buy-in using WizBucks wallet.

    Deducts from the wallet and logs to the ledger atomically via
    ``wb_ledger.append_transaction``.
    """
    team = normalize_team_abbr(team, managers_data=managers_data)

    if round not in BUY_IN_COSTS:
        raise ValueError(f"Invalid round {round}. Only rounds 1-3 require buy-ins.")

    # Find matching keeper pick.
    matches: list[tuple[int, dict]] = []
    for i, p in enumerate(draft_order):
        if not isinstance(p, dict):
            continue
        if p.get("draft") != "keeper":
            continue
        if int(p.get("round") or 0) != int(round):
            continue
        if pick is not None and int(p.get("pick") or 0) != int(pick):
            continue
        if str(p.get("original_owner") or "").strip().upper() != team.strip().upper():
            continue
        matches.append((i, p))

    if not matches:
        suffix = f" pick {pick}" if pick is not None else ""
        raise ValueError(f"No Round {round}{suffix} keeper buy-in pick found for original owner {team}")

    if len(matches) > 1:
        raise ValueError(f"Multiple Round {round} keeper buy-in picks found for original owner {team}; specify pick")

    pick_index, pick_entry = matches[0]

    current_owner = str(pick_entry.get("current_owner") or "").strip().upper()
    if str(source or "").strip().lower() == "buyin_api" and current_owner != team.strip().upper():
        raise ValueError(
            f"Round {round} buy-in must be purchased by the original owner while they still own the pick. "
            f"Current owner is {current_owner or 'UNOWNED'}."
        )

    if not pick_entry.get("buyin_required"):
        raise ValueError(f"Round {round} buy-in is not required for {team}")

    if pick_entry.get("buyin_purchased"):
        raise ValueError(f"Round {round} buy-in already purchased for {team}")

    expected_cost = int(BUY_IN_COSTS[round])
    try:
        file_cost = int(pick_entry.get("buyin_cost") or expected_cost)
    except Exception:
        file_cost = expected_cost
    cost = file_cost

    # Check balance via wb_ledger
    balance_before = _wb_balance(team)
    if balance_before < cost:
        raise ValueError(f"Insufficient WizBucks balance. Need ${cost}, have ${balance_before}")

    # Apply pick flag updates
    now_iso = _now_iso(now)
    pick_entry["buyin_purchased"] = True
    pick_entry["buyin_purchased_at"] = now_iso
    pick_entry["buyin_purchased_by"] = purchased_by
    draft_order[pick_index] = pick_entry

    # Deduct via wb_ledger (writes wallet + ledger atomically)
    trade_part = f"_{trade_id}" if trade_id else ""
    ledger_entry = _wb_append(
        team=team.strip().upper(),
        amount=-cost,
        transaction_type="buyin_purchase",
        description=f"Round {round} keeper draft buy-in purchase",
        metadata={
            "season": season,
            "draft": "keeper",
            "round": round,
            "pick": int(pick_entry.get("pick") or 0),
            "purchased_by": purchased_by,
            "source": source,
            "trade_id": trade_id,
        },
        txn_id=f"wb_{season}_BUYIN_{team}_R{round}_P{int(pick_entry.get('pick') or 0)}{trade_part}_{int((now or datetime.now(timezone.utc)).timestamp())}",
        timestamp=now_iso,
    )
    balance_after = ledger_entry["balance_after"]

    return BuyinApplyResult(
        team=team.strip().upper(),
        round=int(round),
        pick=int(pick_entry.get("pick") or (pick or 0)),
        cost=cost,
        wallet_balance_before=balance_before,
        wallet_balance_after=balance_after,
        ledger_entry=ledger_entry,
    )


def apply_keeper_buyin_refund(
    *,
    team: str,
    round: int,
    pick: Optional[int],
    draft_order: list[dict],
    managers_data: dict,
    refunded_by: str,
    source: str,
    season: int = 2026,
    now: Optional[datetime] = None,
    # Deprecated — ignored.
    wizbucks_data: dict | None = None,
    ledger: list[dict] | None = None,
) -> BuyinRefundResult:
    """Refund a keeper draft buy-in to WizBucks wallet via ``wb_ledger``."""
    team = normalize_team_abbr(team, managers_data=managers_data)

    if round not in BUY_IN_COSTS:
        raise ValueError(f"Invalid round {round}. Only rounds 1-3 require buy-ins.")

    matches: list[tuple[int, dict]] = []
    for i, p in enumerate(draft_order):
        if not isinstance(p, dict):
            continue
        if p.get("draft") != "keeper":
            continue
        if int(p.get("round") or 0) != int(round):
            continue
        if pick is not None and int(p.get("pick") or 0) != int(pick):
            continue
        if str(p.get("original_owner") or "").strip().upper() != team.strip().upper():
            continue
        matches.append((i, p))

    if not matches:
        suffix = f" pick {pick}" if pick is not None else ""
        raise ValueError(f"No Round {round}{suffix} keeper buy-in pick found for original owner {team}")

    if len(matches) > 1:
        raise ValueError(f"Multiple Round {round} keeper buy-in picks found for original owner {team}; specify pick")

    pick_index, pick_entry = matches[0]

    if not pick_entry.get("buyin_purchased"):
        raise ValueError(f"Round {round} buy-in has not been purchased for {team}")

    try:
        refund_amount = int(pick_entry.get("buyin_cost") or BUY_IN_COSTS[round])
    except Exception:
        refund_amount = int(BUY_IN_COSTS[round])

    now_iso = _now_iso(now)

    pick_entry["buyin_purchased"] = False
    pick_entry["buyin_purchased_at"] = None
    pick_entry["buyin_purchased_by"] = None
    draft_order[pick_index] = pick_entry

    # Refund via wb_ledger
    ledger_entry = _wb_append(
        team=team.strip().upper(),
        amount=refund_amount,
        transaction_type="buyin_refund",
        description=f"Round {round} keeper draft buy-in refund",
        metadata={
            "season": season,
            "draft": "keeper",
            "round": round,
            "pick": int(pick_entry.get("pick") or 0),
            "refunded_by": refunded_by,
            "source": source,
        },
        txn_id=f"wb_{season}_BUYIN_REFUND_{team}_R{round}_P{int(pick_entry.get('pick') or 0)}_{int((now or datetime.now(timezone.utc)).timestamp())}",
        timestamp=now_iso,
    )

    return BuyinRefundResult(
        team=team.strip().upper(),
        round=int(round),
        pick=int(pick_entry.get("pick") or (pick or 0)),
        amount=refund_amount,
        wallet_balance_before=ledger_entry["balance_before"],
        wallet_balance_after=ledger_entry["balance_after"],
        ledger_entry=ledger_entry,
    )
