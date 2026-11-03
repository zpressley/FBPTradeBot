"""Auction database transactions (specs/02_auction_portal.md, "How writes stay safe").

This is the only code that touches the auction_* tables. Every function takes a
psycopg connection, opens its own `with conn.transaction():` and (except the
read-only get_board) takes pg_advisory_xact_lock(hashtext('auction')) first, so
auction writes take turns. Rules live in auction.rules; nothing here decides a
rule except the few the rules module has no function for (see record_decision).

Use a connection that is idle (autocommit on, or no query run yet) when you call
these, so `conn.transaction()` is the outermost transaction and commits on exit.
Inside a caller's own transaction they become savepoints and the caller commits.

Errors: rules.BidError (message is safe to show a manager). Discord is never
posted from here: functions return an "announce" text for the caller, and
mark_announced() records that the post went out.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Callable, Optional, Sequence

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from auction import rules
from auction.rules import Bid, BidError, Phase, Prospect
from league import actions

IN_SEASON = "IN_SEASON"
ACTIVE_PHASES = (rules.OB_WINDOW, rules.CB_WINDOW, rules.OB_FINAL)


# ------------------------------------------------------------------ plumbing

def _one(conn, sql, params=()):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _all(conn, sql, params=()):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _exec(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)


def _lock(conn):
    _exec(conn, "select pg_advisory_xact_lock(hashtext('auction'))")


def _date(v) -> date:
    return v if isinstance(v, date) and not isinstance(v, datetime) else date.fromisoformat(str(v))


def _season(calendar, week_start: date) -> int:
    return int(calendar.get("season", week_start.year))


def _week(conn, week_start, for_update=False):
    return _one(conn, "select * from auction_weeks where week_start = %s" + (" for update" if for_update else ""),
                (week_start,))


def _bid_rows(conn, week_start):
    """Live (not removed) bids, oldest first. xmin breaks placed_at ties in insertion order."""
    return _all(conn, """select bid_id, team_abbr, upid, bid_type, amount, placed_at, source
                           from auction_bids where week_start = %s and removed_at is null
                          order by placed_at, xmin::text::bigint""", (week_start,))


def _to_bids(rows) -> list:
    return [Bid(r["team_abbr"], r["upid"], r["bid_type"], r["amount"], r["placed_at"]) for r in rows]


def _balance(conn, team) -> int:
    r = _one(conn, "select coalesce(sum(balance), 0)::int as b from wb_balances where team_abbr = %s", (team,))
    return r["b"]


def _prospect(conn, upid) -> Prospect:
    """Eligible = a Farm player with no open contract AND no open roster_assignments row.
    (roster_assignments is the roster until the Dec 15 cutover; contracts after. Both are checked.)"""
    r = _one(conn, """select p.name, p.player_type,
                             exists (select 1 from contracts c where c.upid = p.upid and c.valid_to is null) as has_contract,
                             exists (select 1 from roster_assignments a where a.upid = p.upid and a.valid_to is null) as on_roster
                        from players p where p.upid = %s""", (upid,))
    if r is None:
        return Prospect(upid, False, "Prospect not found or not eligible.")
    if r["player_type"] != "Farm":
        return Prospect(upid, False, f"{r['name']} is not a Farm prospect.")
    if r["has_contract"] or r["on_roster"]:
        return Prospect(upid, False, f"{r['name']} is already owned and can't be bid on.")
    return Prospect(upid, True)


def _names(conn, upids) -> dict:
    if not upids:
        return {}
    return {r["upid"]: r["name"] for r in _all(conn, "select upid, name from players where upid = any(%s)", (list(upids),))}


def _check_order(conn, order: Sequence[str]) -> list:
    order = list(order)
    if len(set(order)) != len(order):
        raise BidError("Priority order lists a team twice.")
    active = {r["abbr"] for r in _all(conn, "select abbr from teams where active")}
    if set(order) != active:
        raise BidError("Priority order must list every active team exactly once "
                       f"(missing {sorted(active - set(order))}, unknown {sorted(set(order) - active)}).")
    return order


def _ensure_week(conn, phase: Phase, calendar, priority_source):
    """The week row for `phase`, created (with the snapshotted priority order) on first use in an
    active phase. Caller holds the auction lock. None if there is no row and none may be created."""
    week = _week(conn, phase.week_start)
    if week is not None or phase.name not in ACTIVE_PHASES or priority_source is None:
        return week
    order = _check_order(conn, priority_source())
    _exec(conn, "insert into auction_weeks (week_start, season, priority_order) values (%s, %s, %s)",
          (phase.week_start, _season(calendar, phase.week_start), order))
    return _week(conn, phase.week_start)


# ------------------------------------------------------------------ week

def get_or_create_week(conn, now: datetime, calendar, priority_source: Callable[[], Sequence[str]]):
    """The auction_weeks row (dict) for the active week, created on first use. `priority_source` is a
    callable returning the team order, worst standings first. Returns None in off_week/processing
    when no row exists (nothing to open)."""
    with conn.transaction():
        _lock(conn)
        return _ensure_week(conn, rules.phase_for(now, calendar), calendar, priority_source)


# ------------------------------------------------------------------ bids

def place_bid(conn, team, upid, bid_type, amount, source, now: datetime, calendar, priority_source=None):
    """Validate with rules.validate_bid and insert. On BidError nothing is written.
    `priority_source` (optional, same as get_or_create_week) lets the first bid of a week create it."""
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        raise BidError("Bid amount must be a whole number.")
    with conn.transaction():
        _lock(conn)
        phase = rules.phase_for(now, calendar)
        week = _ensure_week(conn, phase, calendar, priority_source)
        if week is None:
            if phase.name in ACTIVE_PHASES:
                raise BidError("This auction week has not been opened yet.")
            # off_week / processing: let the rules say why (they check the phase first)
            rules.validate_bid(Bid(team, upid, bid_type, amount, now), phase, [], 0, [team], now)
            raise BidError("Auctions are not active right now.")
        if week["status"] != "open":
            raise BidError("This auction week is already resolved.")

        order = week["priority_order"]
        rows = _bid_rows(conn, week["week_start"])
        bids = _to_bids(rows)
        balance = _balance(conn, team)
        rules.validate_bid(Bid(team, upid, bid_type, amount, now), phase, bids, balance, order, now,
                           _prospect(conn, upid))

        row = _one(conn, """insert into auction_bids (week_start, team_abbr, upid, bid_type, amount,
                                                      placed_at, placed_date_et, source)
                            values (%s, %s, %s, %s, %s, %s, %s, %s) returning bid_id""",
                   (week["week_start"], team, upid, bid_type, amount, now,
                    now.astimezone(rules.ET).date(), source))
        after = bids + [Bid(team, upid, bid_type, amount, now)]
        held = rules.committed(team, after, order)
        name = _names(conn, [upid]).get(upid, upid)
        kind = "Originating bid" if bid_type == "OB" else "Challenge bid"
        msg = (f"{kind} of ${amount} placed on {name}. You now hold the high bid. "
               f"${balance - held} WB available (total ${balance}, committed ${held}).")
        return {"bid_id": str(row["bid_id"]), "week_start": week["week_start"], "team": team, "upid": upid,
                "name": name, "bid_type": bid_type, "amount": amount, "balance": balance, "committed": held,
                "available": balance - held, "message": msg,
                "announce": f"{team} placed a {bid_type} of ${amount} on {name}."}


def record_decision(conn, team, upid, decision, source, now: datetime, calendar):
    """The OB team's Match/Forfeit in ob_final. One decision per prospect, final.
    auction.rules has no function for this, so the checks are here."""
    decision = str(decision).strip().lower()
    with conn.transaction():
        _lock(conn)
        phase = rules.phase_for(now, calendar)
        if decision not in ("match", "forfeit"):
            raise BidError("Decision must be Match or Forfeit.")
        week = _week(conn, phase.week_start)
        if phase.name != rules.OB_FINAL or week is None:
            raise BidError("Match/Forfeit decisions are only allowed Sat 12:00am to Sun 10:00am ET.")
        if week["status"] != "open":
            raise BidError("This auction week is already resolved.")
        ob = next((b for b in _bid_rows(conn, week["week_start"]) if b["upid"] == upid and b["bid_type"] == "OB"), None)
        if ob is None:
            raise BidError("This prospect has no originating bid.")
        if ob["team_abbr"] != team:
            raise BidError("Only the originating manager can Match or Forfeit.")
        if _one(conn, "select 1 as x from auction_decisions where week_start = %s and upid = %s",
                (week["week_start"], upid)):
            raise BidError("You already recorded a decision on this prospect. It is final.")
        _exec(conn, """insert into auction_decisions (week_start, upid, team_abbr, decision, decided_at, source)
                       values (%s, %s, %s, %s, %s, %s)""", (week["week_start"], upid, team, decision, now, source))
        name = _names(conn, [upid]).get(upid, upid)
        word = "matched" if decision == "match" else "forfeited"
        return {"week_start": week["week_start"], "team": team, "upid": upid, "decision": decision,
                "message": f"You {word} on {name}. This is final.",
                "announce": f"{team} {word} on {name}."}


# ------------------------------------------------------------------ board (read only)

def get_board(conn, now: datetime, calendar, team=None, priority_source=None):
    """Everything the auction page shows. Never writes (no lock, no week creation). If the week row
    does not exist yet, `priority_source` (optional) only supplies the order to display."""
    with conn.transaction():
        phase = rules.phase_for(now, calendar)
        week = _week(conn, phase.week_start)
        if week is not None:
            order = list(week["priority_order"])
        else:
            order = list(priority_source()) if priority_source else []
        rows = _bid_rows(conn, phase.week_start) if week is not None else []
        bids = _to_bids(rows)
        decisions = {r["upid"]: r["decision"] for r in _all(
            conn, "select upid, decision from auction_decisions where week_start = %s", (phase.week_start,))}
        names = _names(conn, {r["upid"] for r in rows})

        prospects = []
        for upid in dict.fromkeys(r["upid"] for r in rows):  # first-bid order
            mine = [r for r in rows if r["upid"] == upid]
            ob = next((r for r in mine if r["bid_type"] == "OB"), None)
            hb = rules.high_bid(upid, bids, order)
            prospects.append({
                "upid": upid, "name": names.get(upid, upid),
                "ob_team": ob["team_abbr"] if ob else None,
                "ob_amount": ob["amount"] if ob else None,
                "high_team": hb[0] if hb else None, "high_amount": hb[1] if hb else None,
                "cb_count": sum(1 for r in mine if r["bid_type"] == "CB"),
                "decision": decisions.get(upid),
                "bids": [{"bid_id": str(r["bid_id"]), "team": r["team_abbr"], "bid_type": r["bid_type"],
                          "amount": r["amount"], "placed_at": r["placed_at"]} for r in mine],
            })
        board = {"week_start": phase.week_start, "season": week["season"] if week else None,
                 "week_status": week["status"] if week else None,
                 "announced_at": week["announced_at"] if week else None,
                 "phase": phase.name, "phase_ends_at": phase.phase_ends_at, "server_now": now,
                 "priority_order": order, "prospects": prospects}
        if team is not None:
            bal = _balance(conn, team)
            held = rules.committed(team, bids, order)
            board["me"] = {"team": team, "balance": bal, "committed": held, "available": bal - held}
        return board


# ------------------------------------------------------------------ resolution

def _announce_text(summary) -> str:
    lines = [f"Auction week of {summary['week_start']} resolved."]
    for r in summary["results"]:
        lines.append(f"{r['team']} won {r['name']} for ${r['amount']}.")
    for u in summary["unsold"]:
        lines.append(f"{u['name']} went unsold.")
    if not summary["results"] and not summary["unsold"]:
        lines.append("No bids this week.")
    return "\n".join(lines)


def resolve_week(conn, week_start, resolved_by, now: datetime):
    """Resolve one week in one transaction. A week already resolved returns its stored summary, so
    running it twice (or at the same moment) changes nothing. Raises LookupError for an unknown week."""
    ws = _date(week_start)
    with conn.transaction():
        _lock(conn)
        week = _week(conn, ws, for_update=True)
        if week is None:
            raise LookupError(f"No auction week starting {ws}.")
        if week["status"] == "skipped":
            raise BidError("This auction week was skipped.")
        if week["status"] == "resolved":
            return _resolve_reply(week["summary"], already=True)

        order = list(week["priority_order"])
        season = week["season"]
        rows = _bid_rows(conn, ws)
        decisions = {r["upid"]: r["decision"] for r in _all(
            conn, "select upid, decision from auction_decisions where week_start = %s", (ws,))}
        balances = {t: _balance(conn, t) for t in order}
        results = rules.resolve(_to_bids(rows), decisions, balances, order)
        names = _names(conn, {r["upid"] for r in rows})

        for r in results:
            _exec(conn, """insert into auction_results (week_start, upid, team_abbr, amount, outcome, dropped)
                           values (%s, %s, %s, %s, %s, %s)""",
                  (ws, r.upid, r.team, r.amount, r.outcome, Jsonb(r.dropped)))
        # Same lock order as league.actions (team lock, then the ledger's wb: lock), teams sorted.
        for t in sorted({r.team for r in results}):
            _exec(conn, "select pg_advisory_xact_lock(hashtext('team:' || %s))", (t,))
        for r in results:
            name = names.get(r.upid, r.upid)
            # acquired_via='auction' opens the PC contract without charging; the debit is below.
            actions.assign_prospect_contract(conn, r.upid, r.team, "PC", acquired_via="auction",
                                             created_by=resolved_by, season=season, now=now)
            _exec(conn, "select wb_spend(%s, %s, 'auction_win', %s, %s, %s, 'auction', %s, %s, %s)",
                  (r.team, r.amount, IN_SEASON, season, r.upid, ws.isoformat(),
                   f"Auction win: {name}", resolved_by))
            _event(conn, r, name, season, resolved_by, now)

        won = {r.upid for r in results}
        summary = {
            "week_start": ws.isoformat(), "season": season, "resolved_by": resolved_by,
            "results": [{"upid": r.upid, "name": names.get(r.upid, r.upid), "team": r.team, "amount": r.amount,
                         "outcome": r.outcome, "dropped": r.dropped} for r in results],
            "unsold": [{"upid": u, "name": names.get(u, u)} for u in dict.fromkeys(x["upid"] for x in rows) if u not in won],
            "total_spent": sum(r.amount for r in results),
        }
        _exec(conn, """update auction_weeks set status = 'resolved', resolved_at = %s, resolved_by = %s, summary = %s
                        where week_start = %s""", (now, resolved_by, Jsonb(summary), ws))
        return _resolve_reply(summary, already=False)


def _event(conn, r, name, season, by, ts):
    text = f"{r.team} won {name} in the auction for ${r.amount} ({r.outcome})."
    actions._event(conn, r.upid, "auction_win", text, team=r.team, season=season, by=by, ts=ts, contract="PC")


def _resolve_reply(summary, already: bool) -> dict:
    n = len(summary["results"])
    msg = (f"Week of {summary['week_start']} was already resolved." if already
           else f"Resolved the week of {summary['week_start']}: {n} prospect(s) sold for ${summary['total_spent']}.")
    return {"already_resolved": already, "summary": summary, "message": msg, "announce": _announce_text(summary)}


def mark_announced(conn, week_start) -> bool:
    """Record that the Discord announcement went out. True if this call set it, False if already set
    (or the week is not resolved)."""
    with conn.transaction():
        _lock(conn)
        with conn.cursor() as cur:
            cur.execute("""update auction_weeks set announced_at = now()
                            where week_start = %s and status = 'resolved' and announced_at is null""",
                        (_date(week_start),))
            return cur.rowcount == 1


# ------------------------------------------------------------------ admin

def admin_remove_bid(conn, bid_id, admin, note=None):
    """Mark a bid removed (never a delete). An OB with live challenge bids can't be removed first."""
    with conn.transaction():
        _lock(conn)
        bid = _one(conn, """select b.*, w.status from auction_bids b join auction_weeks w using (week_start)
                             where b.bid_id = %s for update of b""", (str(bid_id),))
        if bid is None:
            raise BidError("No such bid.")
        if bid["removed_at"] is not None:
            raise BidError("That bid was already removed.")
        if bid["status"] != "open":
            raise BidError("That auction week is already resolved.")
        if bid["bid_type"] == "OB" and _one(
                conn, """select 1 as x from auction_bids where week_start = %s and upid = %s
                            and bid_type = 'CB' and removed_at is null""", (bid["week_start"], bid["upid"])):
            raise BidError("Remove the challenge bids on this prospect before removing its originating bid.")
        _exec(conn, "update auction_bids set removed_at = now(), removed_by = %s, admin_note = %s where bid_id = %s",
              (admin, note, str(bid_id)))
        name = _names(conn, [bid["upid"]]).get(bid["upid"], bid["upid"])
        return {"bid_id": str(bid_id), "message": f"Removed {bid['team_abbr']}'s {bid['bid_type']} of ${bid['amount']} on {name}."}


def admin_set_priority(conn, week_start, order, admin):
    """Override the week's snapshotted priority order (worst standings first). Open weeks only."""
    ws = _date(week_start)
    with conn.transaction():
        _lock(conn)
        week = _week(conn, ws, for_update=True)
        if week is None:
            raise BidError("No auction week starting that date.")
        if week["status"] != "open":
            raise BidError("That auction week is already resolved.")
        order = _check_order(conn, order)
        _exec(conn, "update auction_weeks set priority_order = %s where week_start = %s", (order, ws))
        return {"week_start": ws, "priority_order": order,
                "message": f"Priority order for the week of {ws} set by {admin}."}
