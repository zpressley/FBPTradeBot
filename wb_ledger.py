"""WizBucks Ledger — single gate for all WB mutations.

Every code path that changes a team's WizBucks balance MUST go through
``append_transaction()``.  That function:

  1. Reads the current balance from ``data/wizbucks.json`` (the wallet).
  2. Computes ``balance_after = balance_before + amount``.
  3. Appends a new entry to ``data/wizbucks_transactions.json`` (the ledger).
  4. Writes the updated balance back to the wallet.

Because every mutation goes through this single gate, the wallet and
ledger stay in sync going forward.

The wallet file format is unchanged (full franchise names as keys, integer
values), so every existing reader — the frontend, bot commands,
``auction_manager.py``, etc. — works without modification.

Usage::

    from wb_ledger import append_transaction, rebuild_wallet_from_ledger

    # Inside a code path that already holds DATA_LOCK:
    entry = append_transaction(
        team="CFL",
        amount=-55,
        transaction_type="buyin_purchase",
        description="Round 1 keeper draft buy-in purchase",
        related_player=None,
        metadata={"season": 2026, "source": "buyin_api"},
    )

    # Standalone repair (run once to reconcile drift):
    rebuild_wallet_from_ledger()
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# Paths (relative to repo root, matching existing conventions)
LEDGER_PATH = "data/wizbucks_transactions.json"
WALLET_PATH = "data/wizbucks.json"
MANAGERS_PATH = "config/managers.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_json(path: str) -> Any:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _load_managers() -> Dict[str, Any]:
    return _load_json(MANAGERS_PATH) or {}


def _abbr_to_full_name() -> Dict[str, str]:
    """Return a mapping of team abbreviation → franchise display name.

    Example: ``{"DRO": "Andromedans", "CFL": "Country Fried Lamb", ...}``
    """
    mgr = _load_managers()
    teams = mgr.get("teams") or {}
    return {
        abbr: (info.get("name") or abbr)
        for abbr, info in teams.items()
        if isinstance(info, dict)
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Core: rebuild wallet from ledger
# ---------------------------------------------------------------------------

def rebuild_wallet_from_ledger(
    *,
    ledger: Optional[List[Dict[str, Any]]] = None,
    save: bool = True,
) -> Dict[str, int]:
    """Recompute all wallet balances by summing the ledger.

    Parameters
    ----------
    ledger : list, optional
        Pre-loaded ledger entries.  If *None*, reads from disk.
    save : bool
        If *True* (default), write the derived wallet to ``WALLET_PATH``.

    Returns
    -------
    dict
        The wallet dict (full franchise names → int balances).
    """

    if ledger is None:
        ledger = _load_json(LEDGER_PATH) or []

    name_map = _abbr_to_full_name()

    # Sum amounts per team abbreviation
    totals: Dict[str, int] = {}
    for txn in ledger:
        team = str(txn.get("team") or "").strip().upper()
        amt = int(txn.get("amount") or 0)
        if team:
            totals[team] = totals.get(team, 0) + amt

    # Build wallet keyed by full franchise name (matches existing format).
    # Include every team from managers.json even if they have zero txns.
    wallet: Dict[str, int] = {}
    for abbr, full_name in name_map.items():
        wallet[full_name] = totals.get(abbr, 0)

    # Include any ledger teams that aren't in managers.json (shouldn't happen
    # in practice, but defensive).
    known_abbrs = set(name_map.keys())
    for abbr, total in totals.items():
        if abbr not in known_abbrs:
            wallet[abbr] = total

    if save:
        _save_json(WALLET_PATH, wallet)

    return wallet


# ---------------------------------------------------------------------------
# Core: append a transaction and sync the wallet
# ---------------------------------------------------------------------------

def append_transaction(
    *,
    team: str,
    amount: int,
    transaction_type: str,
    description: str,
    related_player: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    txn_id: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """Append a WB transaction and rebuild the wallet.

    **Important:** callers that hold ``DATA_LOCK`` should continue to hold
    it around this call so that concurrent mutations don't interleave.
    This function does NOT acquire the lock itself because some callers
    (e.g. trade approval) perform a broader load-modify-save cycle that
    must be atomic with the WB update.

    Parameters
    ----------
    team : str
        FBP team abbreviation (e.g. ``"CFL"``).
    amount : int
        Positive for credits, negative for debits.
    transaction_type : str
        Categorisation (``"buyin_purchase"``, ``"admin_adjustment"``, etc.).
    description : str
        Human-readable description.
    related_player : dict, optional
        ``{"upid": "...", "name": "..."}`` if the txn is player-specific.
    metadata : dict, optional
        Arbitrary extra context (season, source, admin, etc.).
    txn_id : str, optional
        Explicit transaction ID.  If *None*, one is auto-generated.
    timestamp : str, optional
        ISO timestamp.  If *None*, uses current UTC.

    Returns
    -------
    dict
        The full ledger entry that was appended (including computed
        ``balance_before`` and ``balance_after``).
    """

    team_norm = team.strip().upper()
    ts = timestamp or _now_iso()

    # Load current ledger and wallet
    ledger: List[Dict[str, Any]] = _load_json(LEDGER_PATH) or []
    wallet: Dict[str, int] = _load_json(WALLET_PATH) or {}
    name_map = _abbr_to_full_name()
    full_name = name_map.get(team_norm, team_norm)

    # Read balance_before from the wallet (source of truth)
    balance_before = int(wallet.get(full_name, 0))
    balance_after = balance_before + int(amount)

    if txn_id is None:
        epoch = int(datetime.now(timezone.utc).timestamp())
        txn_id = f"wb_{team_norm}_{transaction_type}_{epoch}"

    entry: Dict[str, Any] = {
        "txn_id": txn_id,
        "timestamp": ts,
        "team": team_norm,
        "amount": int(amount),
        "balance_before": balance_before,
        "balance_after": balance_after,
        "transaction_type": transaction_type,
        "description": description,
        "related_player": related_player,
        "metadata": metadata or {},
    }

    # Append to ledger
    ledger.append(entry)
    _save_json(LEDGER_PATH, ledger)

    # Update wallet with new balance
    wallet[full_name] = balance_after
    _save_json(WALLET_PATH, wallet)

    return entry


# ---------------------------------------------------------------------------
# Convenience readers
# ---------------------------------------------------------------------------

def get_balance(team: str) -> int:
    """Read a team's current wallet balance.

    Works with either abbreviation (``"CFL"``) or full name
    (``"Country Fried Lamb"``).
    """

    wallet: Dict[str, int] = _load_json(WALLET_PATH) or {}

    # Try direct key first (handles full name lookups)
    if team in wallet:
        return int(wallet[team])

    # Try abbreviation → full name mapping
    name_map = _abbr_to_full_name()
    full = name_map.get(team.strip().upper())
    if full and full in wallet:
        return int(wallet[full])

    return 0


# ---------------------------------------------------------------------------
# CLI: standalone rebuild for repair
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Rebuilding wizbucks.json from ledger...")
    wallet = rebuild_wallet_from_ledger()
    for name, bal in sorted(wallet.items()):
        print(f"  {name:<25} ${bal}")
    print(f"\nWrote {WALLET_PATH} with {len(wallet)} teams.")
