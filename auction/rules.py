"""Auction rules: pure functions, no I/O, no database (specs/02_auction_portal.md).

Ported from auction_manager.py (2026). Differences from 2026, all per the spec:
- an uncontested OB pays its own amount (2026: flat $10)
- the affordability loop re-checks every team after each drop (2026 bug 6)
- bid history is passed in; nothing is read or written here

All league boundaries are America/New_York wall-clock times. Callers may pass
any tz-aware datetime; it is converted to ET before comparing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

OFF_WEEK = "off_week"
OB_WINDOW = "ob_window"
CB_WINDOW = "cb_window"
OB_FINAL = "ob_final"
PROCESSING = "processing"

MIN_OB = 10
MIN_RAISE = 5
_UNRANKED = 10_000  # seed for a team missing from priority_order


class BidError(Exception):
    """A bid broke a rule. str(error) is the message shown to the manager."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Phase:
    name: str
    week_start: date  # Monday (ET) of the auction week
    phase_ends_at: Optional[datetime]  # aware ET datetime, None if nothing follows


@dataclass(frozen=True)
class Bid:
    team: str
    upid: str
    bid_type: str  # 'OB' or 'CB'
    amount: int
    placed_at: datetime  # tz-aware


@dataclass(frozen=True)
class Prospect:
    """Eligibility is decided by the caller (unowned Farm player); not looked up here."""

    upid: str
    eligible: bool = True
    reason: str = ""


@dataclass
class Result:
    upid: str
    team: str
    amount: int
    outcome: str  # 'OB_ONLY' | 'OB_MATCH' | 'CB_WIN'
    dropped: list = field(default_factory=list)  # teams dropped for affordability, in order


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------

def _to_date(v) -> date:
    return v if isinstance(v, date) and not isinstance(v, datetime) else date.fromisoformat(str(v))


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _at(d: date, h: int, m: int = 0) -> datetime:
    """Wall-clock ET datetime; zoneinfo picks the right UTC offset for that date."""
    return datetime(d.year, d.month, d.day, h, m, tzinfo=ET)


def _week_active(monday: date, cal: Mapping) -> bool:
    start = _monday(_to_date(cal["start"]))
    asb = _monday(_to_date(cal["all_star_break_start"]))
    restart = _monday(_to_date(cal["restart"]))
    playoffs = _monday(_to_date(cal["playoffs_start"]))
    if monday < start or monday >= playoffs:
        return False
    return not (asb <= monday < restart)


def _next_open(after: datetime, from_monday: date, cal: Mapping) -> Optional[datetime]:
    for k in range(120):
        m = from_monday + timedelta(weeks=k)
        if _week_active(m, cal):
            opens = _at(m, 15)
            if opens > after:
                return opens
    return None


def phase_for(now: datetime, calendar: Mapping) -> Phase:
    """Phase at `now`. `calendar` has start, all_star_break_start, restart, playoffs_start."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    et = now.astimezone(ET)
    d, t, wd = et.date(), et.time().replace(tzinfo=None), et.weekday()
    monday = _monday(d)

    if wd == 0 and t < time(15):
        prev = monday - timedelta(days=7)
        if _week_active(prev, calendar):
            return Phase(PROCESSING, prev, _at(monday, 15))
        return Phase(OFF_WEEK, monday, _next_open(et, monday, calendar))

    if not _week_active(monday, calendar):
        return Phase(OFF_WEEK, monday, _next_open(et, monday, calendar))

    if wd in (0, 1):
        return Phase(OB_WINDOW, monday, _at(monday + timedelta(days=2), 0))
    if wd in (2, 3) or (wd == 4 and t < time(21)):
        return Phase(CB_WINDOW, monday, _at(monday + timedelta(days=4), 21))
    if wd == 4:  # Fri 9pm to Sat midnight: the spec's table has no phase here
        return Phase(OFF_WEEK, monday, _at(monday + timedelta(days=5), 0))
    if wd == 5 or (wd == 6 and t < time(10)):
        return Phase(OB_FINAL, monday, _at(monday + timedelta(days=6), 10))
    return Phase(PROCESSING, monday, _at(monday + timedelta(days=7), 15))


# ---------------------------------------------------------------------------
# Standing of a prospect: high bid and committed WizBucks
# ---------------------------------------------------------------------------

def _seed(team: str, priority_order: Sequence[str]) -> int:
    try:
        return list(priority_order).index(team)
    except ValueError:
        return _UNRANKED


def high_bid(upid: str, bids: Sequence[Bid], priority_order: Sequence[str]):
    """(team, amount) holding the high bid on a prospect, or None. OB counts as a bid.

    No ties: an equal amount belongs to the lower seed (earlier in priority_order).
    """
    best = None
    for b in bids:
        if b.upid != upid:
            continue
        key = (-b.amount, _seed(b.team, priority_order))
        if best is None or key < best[0]:
            best = (key, b)
    return None if best is None else (best[1].team, best[1].amount)


def committed(team: str, bids: Sequence[Bid], priority_order: Sequence[str]) -> int:
    """Sum of the prospects on which `team` is currently the high bidder."""
    total = 0
    for upid in {b.upid for b in bids}:
        hb = high_bid(upid, bids, priority_order)
        if hb and hb[0] == team:
            total += hb[1]
    return total


# ---------------------------------------------------------------------------
# Bid validation
# ---------------------------------------------------------------------------

def validate_bid(bid: Bid, week: Phase, bids: Sequence[Bid], balance: int,
                 priority_order: Sequence[str], now: datetime,
                 prospect: Optional[Prospect] = None, min_ob: int = MIN_OB,
                 min_raise: int = MIN_RAISE) -> None:
    """Raise BidError if `bid` breaks a rule. `week` is phase_for(now, ...);
    `bids` are the week's existing, non-removed bids; `balance` is the team's WB.
    `min_ob` and `min_raise` come from wb_prices (auction_min_ob, auction_min_raise); the
    defaults are the 2027 values."""
    et = now.astimezone(ET)
    team, upid, amount = bid.team, bid.upid, int(bid.amount)

    if week.name == OFF_WEEK:
        raise BidError("Auctions are not active right now.")
    if week.name == PROCESSING:
        raise BidError("Auction is in processing; bids are closed.")
    if bid.bid_type not in ("OB", "CB"):
        raise BidError("Invalid bid type. Use OB or CB.")
    if team not in priority_order:
        raise BidError(f"Unknown team: {team}")
    if amount <= 0:
        raise BidError("Bid amount must be positive.")
    if prospect is not None and not prospect.eligible:
        raise BidError(prospect.reason or "Prospect not found or not eligible.")

    if bid.bid_type == "OB" and week.name != OB_WINDOW:
        raise BidError("Originating bids are only allowed Mon 3pm–Tue midnight ET.")
    if bid.bid_type == "CB" and week.name != CB_WINDOW:
        raise BidError("Challenge bids are only allowed Wed midnight–Fri 9:00pm ET.")
    if bid.bid_type == "OB" and amount < min_ob:
        raise BidError(f"Originating bids must be at least ${min_ob} WB.")

    if bid.bid_type == "OB":
        if any(b.team == team and b.bid_type == "OB" for b in bids):
            raise BidError("You have already placed an originating bid this week.")
        if any(b.upid == upid and b.bid_type == "OB" for b in bids):
            raise BidError("This prospect already has an originating bid.")
    else:
        ob = next((b for b in bids if b.upid == upid and b.bid_type == "OB"), None)
        if ob is None:
            raise BidError("Challenge bids require an existing originating bid.")
        if ob.team == team:
            raise BidError("Originating manager cannot place challenge bids on their own OB.")

        high_team, high_amt = high_bid(upid, bids, priority_order)
        if amount < high_amt + min_raise:
            if amount == high_amt:
                if _seed(team, priority_order) >= _seed(high_team, priority_order):
                    raise BidError(
                        f"To priority-match at ${high_amt}, you need worse standings "
                        f"(higher CB priority) than the current high bidder ({high_team})."
                    )
            else:
                raise BidError(
                    f"Challenge bids must be at least ${min_raise} above the current high (${high_amt}), "
                    f"or match at ${high_amt} exactly if you have higher CB priority (worse standings)."
                )

        my_cbs = [b for b in bids if b.team == team and b.upid == upid and b.bid_type == "CB"]
        if et.weekday() == 4 and not my_cbs:
            raise BidError(
                "Friday CBs require at least one prior CB on this prospect earlier in the week. "
                "No last-minute spoiler bids."
            )
        if any(b.placed_at.astimezone(ET).date() == et.date() for b in my_cbs) and high_team != team:
            raise BidError("You already have a challenge bid on this prospect today.")

    committed_now = committed(team, bids, priority_order)
    available = balance - committed_now
    hb = high_bid(upid, bids, priority_order)
    if bid.bid_type == "CB" and hb and hb[0] == team:
        available += hb[1]  # raising your own high bid replaces that commitment
    if amount > available:
        raise BidError(
            f"Insufficient WB. You have ${available} available "
            f"(total ${balance}, committed ${committed_now})."
        )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _winner(upid: str, pbids: Sequence[Bid], decision: Optional[str],
            excluded: Sequence[str], priority_order: Sequence[str]):
    """Per-prospect rule on the bids of teams not excluded.
    Returns (team, amount, outcome, when) or None. `when` is what orders a team's wins when it
    can't afford them all: the OB's placed_at for OB_ONLY and OB_MATCH (the OB team's win starts
    with its OB), the winning CB's placed_at for CB_WIN."""
    live = [b for b in pbids if b.team not in excluded]
    ob = next((b for b in live if b.bid_type == "OB"), None)
    cbs = [b for b in live if b.bid_type == "CB"]
    if not cbs:
        return None if ob is None else (ob.team, ob.amount, "OB_ONLY", ob.placed_at)
    top = max(b.amount for b in cbs)
    top_cbs = sorted((b for b in cbs if b.amount == top),
                     key=lambda b: (_seed(b.team, priority_order), b.placed_at))
    cb = top_cbs[0]
    if ob is not None and decision == "match":
        return (ob.team, cb.amount, "OB_MATCH", ob.placed_at)
    return (cb.team, cb.amount, "CB_WIN", cb.placed_at)


def resolve(bids: Sequence[Bid], decisions: Mapping[str, str],
            balances: Mapping[str, int], priority_order: Sequence[str]) -> list:
    """Winners for the week. `decisions` maps upid -> 'match' | 'forfeit' (OB team's choice).

    Prospects nobody can afford produce no Result. Results are ordered by when
    the prospect's first bid was placed, then upid.

    Affordability: while any team's wins cost more than its balance, the over-budget team that is
    first in `priority_order` (worst standings first) is handled, and its LATEST win is dropped:
    latest = the OB's placed_at for OB_ONLY and OB_MATCH, the winning CB's placed_at for CB_WIN
    (ties: the larger upid). The prospect is then re-decided without that team, and every team is
    checked again. The order is a choice for Zach to confirm (auction/REPLAY_NOTES.md).
    """
    by_upid: dict = {}
    for b in bids:
        by_upid.setdefault(b.upid, []).append(b)
    order = sorted(by_upid, key=lambda u: (min(b.placed_at for b in by_upid[u]), u))
    excluded = {u: [] for u in order}

    def winners():
        out = {}
        for u in order:
            w = _winner(u, by_upid[u], decisions.get(u), excluded[u], priority_order)
            if w:
                out[u] = w
        return out

    while True:
        won = winners()
        spend: dict = {}
        for u, (team, amount, _o, _t) in won.items():
            spend[team] = spend.get(team, 0) + amount
        over = [t for t in spend if spend[t] > balances.get(t, 0)]
        if not over:
            break
        # Deterministic: worst standings first. Every team is re-checked next pass.
        team = min(over, key=lambda t: (_seed(t, priority_order), t))
        mine = [(w[3], u) for u, w in won.items() if w[0] == team]
        _, latest = max(mine)  # latest win dropped first
        excluded[latest].append(team)

    return [Result(u, w[0], w[1], w[2], list(excluded[u])) for u, w in won.items()]
