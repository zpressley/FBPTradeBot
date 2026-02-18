from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional


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
    kap_balance_before: int
    kap_balance_after: int
    ledger_entry: dict[str, Any]


@dataclass(frozen=True)
class BuyinRefundResult:
    team: str
    round: int
    pick: int
    amount: int
    kap_balance_before: int
    kap_balance_after: int
    ledger_entry: dict[str, Any]


def _now_iso(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def get_kap_balance(team: str, managers_data: dict, season: int = 2026) -> int:
    team_data = (managers_data.get("teams") or {}).get(team)
    if not isinstance(team_data, dict):
        return 0

    wiz = team_data.get("wizbucks") or {}
    season_data = (wiz.get(str(season)) or {}) if isinstance(wiz, dict) else {}
    allotments = (season_data.get("allotments") or {}) if isinstance(season_data, dict) else {}
    kap = (allotments.get("KAP") or {}) if isinstance(allotments, dict) else {}
    try:
        return int(kap.get("total") or 0)
    except Exception:
        return 0


def update_kap_balance(team: str, delta: int, managers_data: dict, season: int = 2026) -> None:
    teams = managers_data.get("teams")
    if not isinstance(teams, dict) or team not in teams or not isinstance(teams.get(team), dict):
        raise ValueError(f"Team {team} not found in managers.json")

    team_data = teams[team]
    wiz = team_data.get("wizbucks")
    if not isinstance(wiz, dict):
        raise ValueError(f"Team {team} missing wizbucks config")

    season_data = wiz.get(str(season))
    if not isinstance(season_data, dict):
        raise ValueError(f"Team {team} missing wizbucks.{season} config")

    allotments = season_data.get("allotments")
    if not isinstance(allotments, dict):
        raise ValueError(f"Team {team} missing wizbucks.{season}.allotments")

    kap = allotments.get("KAP")
    if not isinstance(kap, dict):
        raise ValueError(f"Team {team} missing wizbucks.{season}.allotments.KAP")

    current_total = int(kap.get("total") or 0)
    kap["total"] = current_total + int(delta)


def apply_keeper_buyin_purchase(
    *,
    team: str,
    round: int,
    pick: Optional[int],
    draft_order: list[dict],
    managers_data: dict,
    ledger: list[dict],
    purchased_by: str,
    source: str,
    trade_id: Optional[str] = None,
    season: int = 2026,
    now: Optional[datetime] = None,
) -> BuyinApplyResult:
    if round not in BUY_IN_COSTS:
        raise ValueError(f"Invalid round {round}. Only rounds 1-3 require buy-ins.")

    # Find matching keeper pick
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
        if str(p.get("current_owner") or "").strip().upper() != team.strip().upper():
            continue
        matches.append((i, p))

    if not matches:
        suffix = f" pick {pick}" if pick is not None else ""
        raise ValueError(f"No Round {round}{suffix} keeper pick found for team {team}")

    if len(matches) > 1:
        raise ValueError(f"Multiple Round {round} keeper picks found for team {team}; specify pick")

    pick_index, pick_entry = matches[0]

    if not pick_entry.get("buyin_required"):
        raise ValueError(f"Round {round} buy-in is not required for {team}")

    if pick_entry.get("buyin_purchased"):
        raise ValueError(f"Round {round} buy-in already purchased for {team}")

    # Use configured cost, falling back to file-provided cost if present.
    expected_cost = int(BUY_IN_COSTS[round])
    try:
        file_cost = int(pick_entry.get("buyin_cost") or expected_cost)
    except Exception:
        file_cost = expected_cost

    cost = file_cost

    balance_before = get_kap_balance(team, managers_data, season=season)
    if balance_before < cost:
        raise ValueError(f"Insufficient KAP balance. Need ${cost}, have ${balance_before}")

    # Apply pick flag updates
    now_iso = _now_iso(now)
    pick_entry["buyin_purchased"] = True
    pick_entry["buyin_purchased_at"] = now_iso
    pick_entry["buyin_purchased_by"] = purchased_by
    draft_order[pick_index] = pick_entry

    # Apply KAP deduction
    update_kap_balance(team, -cost, managers_data, season=season)
    balance_after = get_kap_balance(team, managers_data, season=season)

    # Ledger entry (new-style schema used across the repo)
    trade_part = f"_{trade_id}" if trade_id else ""
    ledger_entry = {
        "txn_id": f"wb_{season}_BUYIN_{team}_R{round}_P{int(pick_entry.get('pick') or 0)}{trade_part}_{int((now or datetime.now(timezone.utc)).timestamp())}",
        "timestamp": now_iso,
        "team": team.strip().upper(),
        "amount": -cost,
        "balance_before": balance_before,
        "balance_after": balance_after,
        "transaction_type": "buyin_purchase",
        "description": f"Round {round} keeper draft buy-in purchase",
        "related_player": None,
        "metadata": {
            "season": season,
            "draft": "keeper",
            "round": round,
            "pick": int(pick_entry.get("pick") or 0),
            "purchased_by": purchased_by,
            "source": source,
            "trade_id": trade_id,
        },
    }

    ledger.append(ledger_entry)

    return BuyinApplyResult(
        team=team.strip().upper(),
        round=int(round),
        pick=int(pick_entry.get("pick") or (pick or 0)),
        cost=cost,
        kap_balance_before=balance_before,
        kap_balance_after=balance_after,
        ledger_entry=ledger_entry,
    )


def apply_keeper_buyin_refund(
    *,
    team: str,
    round: int,
    pick: Optional[int],
    draft_order: list[dict],
    managers_data: dict,
    ledger: list[dict],
    refunded_by: str,
    source: str,
    season: int = 2026,
    now: Optional[datetime] = None,
) -> BuyinRefundResult:
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
        if str(p.get("current_owner") or "").strip().upper() != team.strip().upper():
            continue
        matches.append((i, p))

    if not matches:
        suffix = f" pick {pick}" if pick is not None else ""
        raise ValueError(f"No Round {round}{suffix} keeper pick found for team {team}")

    if len(matches) > 1:
        raise ValueError(f"Multiple Round {round} keeper picks found for team {team}; specify pick")

    pick_index, pick_entry = matches[0]

    if not pick_entry.get("buyin_purchased"):
        raise ValueError(f"Round {round} buy-in has not been purchased for {team}")

    try:
        refund_amount = int(pick_entry.get("buyin_cost") or BUY_IN_COSTS[round])
    except Exception:
        refund_amount = int(BUY_IN_COSTS[round])

    now_iso = _now_iso(now)

    # Update pick
    pick_entry["buyin_purchased"] = False
    pick_entry["buyin_purchased_at"] = None
    pick_entry["buyin_purchased_by"] = None
    draft_order[pick_index] = pick_entry

    balance_before = get_kap_balance(team, managers_data, season=season)
    update_kap_balance(team, refund_amount, managers_data, season=season)
    balance_after = get_kap_balance(team, managers_data, season=season)

    ledger_entry = {
        "txn_id": f"wb_{season}_BUYIN_REFUND_{team}_R{round}_P{int(pick_entry.get('pick') or 0)}_{int((now or datetime.now(timezone.utc)).timestamp())}",
        "timestamp": now_iso,
        "team": team.strip().upper(),
        "amount": refund_amount,
        "balance_before": balance_before,
        "balance_after": balance_after,
        "transaction_type": "buyin_refund",
        "description": f"Round {round} keeper draft buy-in refund",
        "related_player": None,
        "metadata": {
            "season": season,
            "draft": "keeper",
            "round": round,
            "pick": int(pick_entry.get("pick") or 0),
            "refunded_by": refunded_by,
            "source": source,
        },
    }

    ledger.append(ledger_entry)

    return BuyinRefundResult(
        team=team.strip().upper(),
        round=int(round),
        pick=int(pick_entry.get("pick") or (pick or 0)),
        amount=refund_amount,
        kap_balance_before=balance_before,
        kap_balance_after=balance_after,
        ledger_entry=ledger_entry,
    )
