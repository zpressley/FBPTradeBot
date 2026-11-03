"""Trade portal database layer (specs/05_trade_portal.md).

`validate(conn, trade, stage)` is the ONE place trade rules live. Build, send, accept and approve all
call it, and nothing else checks a trade rule. `approve` is the only way a trade is applied.

A `trade` is a plain dict:
    {"season": 2027, "created_by": "WIZ", "teams": ["WIZ", "CFL"], "trade_id": None or "T27-0001",
     "items": [{"kind": "player", "upid": "123", "from_team": "WIZ", "to_team": "CFL"},
               {"kind": "pick", "draft_id": "keeper-2027", "overall": 27, "from_team": ..., "to_team": ...},
               {"kind": "wb", "amount": 15, "from_team": ..., "to_team": ...}]}
`get_trade()` returns the same shape for a stored trade (plus status, parties, ...).

Every function opens its own `with conn.transaction():` and takes pg_advisory_xact_lock locks
(hashtext('trade:<id>') for one trade, then hashtext('team:<abbr>') for each team, the same team locks
league.actions uses). Use a connection with autocommit on so that transaction is the outermost one;
inside a caller's transaction these become savepoints and the caller commits.

Players move through league.actions.transfer (which writes the player_events row), money through
league.actions.wb_spend / wb_credit and admin_adjust. Notifications are rows in the `notifications`
outbox, written in the same transaction as the change (so they exist if and only if it committed);
nothing is sent from here.

Errors: TradeError (a RuleError; message safe to show a manager; .status is the HTTP status the API
should use), NotAdmin (403), TradeConflict (409: the trade was auto-withdrawn because an item moved;
the withdrawal IS committed, the exception only tells the caller). `now` is passed in everywhere so
tests control time; None means the real clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from league import actions
from league.actions import RuleError

ET = ZoneInfo("America/New_York")
STAGES = ("build", "send", "accept", "approve")
EXPIRY_DAYS = 7            # pending offers (decided 9/30); accepted trades never expire
MAX_ACTIVE_OFFERS = 12     # per team, as the sender
MAX_PICK_ROUND = 15        # keeper draft rounds 1-15 only
BUY_IN_ROUNDS = 3          # rounds 1-3 need both teams bought in (Constitution 05.6.3.1)
WB_STEP = 5
MAX_COMMENT = 500


class TradeError(RuleError):
    def __init__(self, message, status=400, issues=None):
        super().__init__(message)
        self.status = status
        self.issues = issues or []


class NotAdmin(TradeError):
    def __init__(self, message="Only an admin can do that."):
        super().__init__(message, status=403)


class TradeConflict(TradeError):
    def __init__(self, message):
        super().__init__(message, status=409)


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
        return cur.rowcount


def _now(now):
    return now if now is not None else datetime.now(timezone.utc)


def _lock_teams(conn, teams):
    for t in sorted({t for t in teams if t}):
        _exec(conn, "select pg_advisory_xact_lock(hashtext('team:' || %s))", (t,))


def _lock_trade(conn, trade_id):
    """Serialise everything that touches one trade, then lock its row."""
    _exec(conn, "select pg_advisory_xact_lock(hashtext('trade:' || %s))", (trade_id,))
    row = _one(conn, "select * from trades_live where trade_id = %s for update", (trade_id,))
    if not row:
        raise TradeError(f"Trade {trade_id} does not exist.", status=404)
    return row


def _item(i):
    return {"kind": i.get("kind"), "upid": None if i.get("upid") is None else str(i["upid"]),
            "draft_id": i.get("draft_id"), "overall": i.get("overall"), "amount": i.get("amount"),
            "from_team": i.get("from_team"), "to_team": i.get("to_team")}


def _require_admin(conn, abbr):
    r = _one(conn, "select role from teams where abbr = %s", (abbr,))
    if not r or r["role"] != "admin":
        raise NotAdmin()


def _name(conn, upid):
    r = _one(conn, "select name from players where upid = %s", (upid,))
    return r["name"] if r else str(upid)


def _pick_label(slot):
    return f"R{slot['round']} P{slot['overall']}"


def _notify(conn, kind, team, payload, ts):
    _exec(conn, "insert into notifications (kind, team_abbr, payload, created_at) values (%s, %s, %s, %s)",
          (kind, team, Jsonb(payload), ts))


def _dm(conn, trade_id, kind, teams, ts, **extra):
    for t in sorted(set(teams)):
        _notify(conn, kind, t, {"trade_id": trade_id, **extra}, ts)


def _system_comment(conn, trade_id, body, ts):
    """Filed under the sender's team (trade_comments.team_abbr is a foreign key)."""
    t = _one(conn, "select created_by from trades_live where trade_id = %s", (trade_id,))
    _exec(conn, "insert into trade_comments (trade_id, team_abbr, kind, body, created_at) values (%s, %s, 'system', %s, %s)",
          (trade_id, t["created_by"], body[:MAX_COMMENT], ts))


# ------------------------------------------------------------------ reading

def get_trade(conn, trade_id):
    """The stored trade in the same shape validate() takes, plus status, parties (team -> accepted_at) and comments."""
    t = _one(conn, "select * from trades_live where trade_id = %s", (trade_id,))
    if not t:
        raise TradeError(f"Trade {trade_id} does not exist.", status=404)
    parties = _all(conn, "select team_abbr, accepted_at from trade_parties where trade_id = %s order by team_abbr", (trade_id,))
    t["parties"] = {p["team_abbr"]: p["accepted_at"] for p in parties}
    t["teams"] = [p["team_abbr"] for p in parties]
    t["items"] = _all(conn, """select i.id, i.kind, i.upid, i.draft_id, i.overall, i.amount, i.from_team, i.to_team, i.raw,
                                      p.name as player_name
                                 from trade_items i left join players p on p.upid = i.upid
                                where i.trade_id = %s order by i.id""", (trade_id,))
    return t


def thread(conn, trade_id):
    """The comment thread. A counter carries the thread over: the whole chain of offers linked by
    countered_from (before and after this one) shows the same comments, oldest first."""
    chain = {r["trade_id"] for r in _all(conn, """
        with recursive up(trade_id, countered_from) as (
            select trade_id, countered_from from trades_live where trade_id = %s
            union select t.trade_id, t.countered_from from trades_live t join up on t.trade_id = up.countered_from),
        chain(trade_id) as (
            select trade_id from up
            union select t.trade_id from trades_live t join chain c on t.countered_from = c.trade_id)
        select trade_id from chain""", (trade_id,))}
    return _all(conn, """select id, trade_id, team_abbr, kind, body, created_at from trade_comments
                          where trade_id = any(%s) order by created_at, id""", (sorted(chain),))


# ------------------------------------------------------------------ validate: facts

def _player_owner(conn, upid):
    """Who owns a player. contracts (live) first; until the Dec 15 cutover the open roster_assignments row
    (the shadow mirror) is the roster of record for 2026 holdings.
    REMOVE THE roster_assignments JOIN BELOW AT CUTOVER, once contracts is the only owner list."""
    return _one(conn, """select coalesce(c.team_abbr, ra.team_abbr) as team, c.upid is not null as has_contract
                           from players p
                           left join contracts c on c.upid = p.upid and c.valid_to is null
                           left join roster_assignments ra on ra.upid = p.upid and ra.valid_to is null   -- mirror: remove at cutover
                          where p.upid = %s""", (upid,))


def _slot(conn, draft_id, overall):
    return _one(conn, """select s.draft_id, s.overall, s.round, s.pick_in_round, s.original_team, s.team_abbr, s.skip_reason,
                                d.season as draft_season, d.draft_type,
                                exists (select 1 from draft_picks_live dp where dp.draft_id = s.draft_id and dp.overall = s.overall) as used
                           from draft_slots s join drafts d using (draft_id)
                          where s.draft_id = %s and s.overall = %s""", (draft_id, overall))


def bought_in(conn, draft_id, team, round_no):
    """A team is bought into round N when its own slot in round N (original_team = team, wherever the
    pick sits now) is live: skip_reason is null. Read from draft_slots, never copied."""
    return _one(conn, """select 1 as x from draft_slots where draft_id = %s and round = %s
                            and original_team = %s and skip_reason is null""", (draft_id, round_no, team)) is not None


def committed_wb(conn, team, except_trade_id=None):
    """WizBucks a team has promised in its OTHER offers: WB items it gives in pending/accepted trades where
    it has accepted (the sender counts as accepted). An offer to a team that has not accepted ties up nothing."""
    r = _one(conn, """select coalesce(sum(i.amount), 0)::int as c
                        from trade_items i
                        join trades_live t on t.trade_id = i.trade_id
                        join trade_parties p on p.trade_id = i.trade_id and p.team_abbr = i.from_team
                       where i.kind = 'wb' and i.from_team = %s and t.status in ('pending', 'accepted')
                         and p.accepted_at is not null and t.trade_id is distinct from %s""", (team, except_trade_id))
    return r["c"]


def _roster_limits(conn, season):
    return _one(conn, "select * from roster_limits where season = %s", (season,))


def _roster_after(conn, items, team):
    """(before, after): the team's active players now and after the trade. An incoming player keeps his
    roster slot (a trade never changes it); one with no roster_slots row yet counts as active."""
    before = actions._active_players(conn, team)
    out = {i["upid"] for i in items if i["kind"] == "player" and i["from_team"] == team}
    incoming = [i["upid"] for i in items if i["kind"] == "player" and i["to_team"] == team]
    after = [p for p in before if p["upid"] not in out]
    if incoming:
        after += _all(conn, """select p.upid, p.name, p.positions, p.mlb_primary_position
                                 from players p left join roster_slots rs on rs.upid = p.upid
                                where p.upid = any(%s) and coalesce(rs.slot, 'active') = 'active'""", (incoming,))
    return before, after


def _counts(players):
    cl = [actions.classify(p["positions"], p["mlb_primary_position"]) for p in players]
    pitchers = sum(c["pitcher"] for c in cl)
    return {"active": len(players), "sp": sum(c["sp"] for c in cl), "pitchers": pitchers, "batters": len(players) - pitchers}


def _roster_warnings(conn, items, team, season):
    lim = _roster_limits(conn, season)
    if not lim:
        return []
    _, after = _roster_after(conn, items, team)
    # catcher_min is a detection-only rule in league.actions (you may need to ADD a catcher), so it is not a trade warning
    return [v for v in actions.roster_violations(after, lim) if v["rule"] != "catcher_min"]


# ------------------------------------------------------------------ validate: the rules

class _Result(dict):
    """{"blocks": [...], "warnings": [...], "ok": bool}; each issue is {"code", "message", "team"}."""

    def __init__(self):
        super().__init__(blocks=[], warnings=[])

    def block(self, code, message, team=None):
        self["blocks"].append({"code": code, "message": message, "team": team})

    def warn(self, code, message, team=None):
        self["warnings"].append({"code": code, "message": message, "team": team})

    @property
    def ok(self):
        return not self["blocks"]


def validate(conn, trade, stage, now=None):
    """The one rule function (spec 05, "Rules"). Returns {"blocks", "warnings", "ok"}; it writes nothing.

    stage 'build'/'send': every rule. 'accept'/'approve': the rules the spec marks for them (ownership,
    window, WizBucks, buy-in, roster warnings); the structural rules cannot change once a trade is stored.
    Issue code 'ownership' is the conflict that auto-withdraws a trade at accept/approve.
    """
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}")
    ts = _now(now)
    r = _Result()
    items = [_item(i) for i in trade.get("items") or []]
    season = trade["season"]
    early = stage in ("build", "send")

    if early:
        _check_structure(conn, trade, items, r)
        if r["blocks"]:
            return r          # later checks assume well-formed items

    # trade window: TRADE must be open; picks also need KAP (admins open and close these in league_windows)
    if not actions.window_is_open(conn, season, "TRADE", ts):
        r.block("window", "The trade window is closed.")
    if any(i["kind"] == "pick" for i in items) and not actions.window_is_open(conn, season, "KAP", ts):
        r.block("window", "Draft picks can only be traded while the KAP window is open.")

    _check_ownership(conn, items, r)
    _check_wb(conn, trade, items, stage, r)
    _check_picks(conn, trade, items, early, r)
    if early:
        _check_two_way(conn, items, r)
        _check_offer_cap(conn, trade, r)
    for team in sorted({i["from_team"] for i in items} | {i["to_team"] for i in items}):
        for v in _roster_warnings(conn, items, team, season):
            r.warn("roster", f"{team}: {v['detail']}", team)
    return r


def _check_structure(conn, trade, items, r):
    teams = list(trade.get("teams") or [])
    sender = trade.get("created_by")
    if len(set(teams)) != len(teams) or len(teams) not in (2, 3):
        r.block("teams", "A trade has 2 or 3 different teams.")
        return
    known = {t["abbr"] for t in _all(conn, "select abbr from teams where active")}
    for t in teams:
        if t not in known:
            r.block("teams", f"{t} is not an active team.", t)
    if sender not in teams:
        r.block("teams", "The sender has to be one of the teams in the trade.")
    if not items:
        r.block("items", "Add something to the trade.")
    seen = set()
    for i in items:
        if i["kind"] not in ("player", "pick", "wb"):
            r.block("items", f"Unknown item type {i['kind']!r}.")
            continue
        if i["from_team"] not in teams or i["to_team"] not in teams or i["from_team"] == i["to_team"]:
            r.block("items", "Every item goes from one team in the trade to a different team in the trade.")
            continue
        if i["kind"] == "player":
            key = ("player", i["upid"])
            if not i["upid"] or not _one(conn, "select 1 as x from players where upid = %s", (i["upid"],)):
                r.block("items", f"Unknown player {i['upid']!r}.")
        elif i["kind"] == "pick":
            key = ("pick", i["draft_id"], i["overall"])
            if i["draft_id"] is None or i["overall"] is None:
                r.block("items", "A pick needs its draft and pick number.")
        else:
            key = ("wb", i["from_team"], i["to_team"])
            amt = i["amount"]
            if not isinstance(amt, int) or amt <= 0 or amt % WB_STEP:
                r.block("wb_step", f"WizBucks move in ${WB_STEP} steps (got {amt!r}).", i["from_team"])
        if key in seen:
            r.block("items", "The same item is in the trade twice.")
        seen.add(key)
    used = {i["from_team"] for i in items} | {i["to_team"] for i in items}
    for t in teams:
        if t not in used:
            r.block("items", f"{t} is in the trade but gives and gets nothing.", t)


def _check_ownership(conn, items, r):
    for i in items:
        if i["kind"] == "player":
            o = _player_owner(conn, i["upid"])
            if not o or o["team"] is None:
                r.block("ownership", f"{_name(conn, i['upid'])} is not on any roster.", i["from_team"])
            elif o["team"] != i["from_team"]:
                r.block("ownership", f"{_name(conn, i['upid'])} is on {o['team']}, not {i['from_team']}.", i["from_team"])
        elif i["kind"] == "pick":
            s = _slot(conn, i["draft_id"], i["overall"])
            if not s:
                r.block("ownership", f"Pick {i['draft_id']} #{i['overall']} does not exist.", i["from_team"])
            elif s["team_abbr"] != i["from_team"]:
                r.block("ownership", f"{_pick_label(s)} belongs to {s['team_abbr']}, not {i['from_team']}.", i["from_team"])


def _check_wb(conn, trade, items, stage, r):
    """WizBucks leaving a team must be covered. Build/send: balance minus what the team already promised in
    its other offers. Accept/approve: the balance (the other offers are not applied yet, and the first one
    approved is the one that gets the money)."""
    out = {}
    for i in items:
        if i["kind"] == "wb":
            out[i["from_team"]] = out.get(i["from_team"], 0) + i["amount"]
    for team, need in sorted(out.items()):
        bal = actions.balance(conn, team)
        tied = committed_wb(conn, team, trade.get("trade_id")) if stage in ("build", "send") else 0
        if bal - tied < need:
            extra = f" and ${tied} is already promised in other offers" if tied else ""
            r.block("wb_balance", f"{team} has ${bal}{extra}; this trade needs ${need}.", team)


def _check_picks(conn, trade, items, early, r):
    for i in items:
        if i["kind"] != "pick":
            continue
        s = _slot(conn, i["draft_id"], i["overall"])
        if not s:
            continue              # reported by the ownership check
        label = _pick_label(s)
        if s["draft_type"] != "keeper" or s["draft_season"] != trade["season"]:
            r.block("pick", f"{label}: only picks in the {trade['season']} keeper draft can be traded.")
            continue
        if s["used"]:
            r.block("pick", f"{label} has already been used.")
        if early and not 1 <= s["round"] <= MAX_PICK_ROUND:
            r.block("pick_round", f"Only rounds 1-{MAX_PICK_ROUND} can be traded ({label}).")
        if s["round"] <= BUY_IN_ROUNDS:
            for team in (i["from_team"], i["to_team"]):
                if not bought_in(conn, s["draft_id"], team, s["round"]):
                    r.block("buyin", f"{team} isn't bought into Round {s['round']}.", team)


def _check_two_way(conn, items, r):
    for i in items:
        if i["kind"] != "player":
            continue
        twin = _one(conn, "select raw ->> '2wayplayer' as twin from players where upid = %s", (i["upid"],))["twin"]
        if twin and not any(o["kind"] == "player" and o["upid"] == str(twin) and o["from_team"] == i["from_team"]
                            and o["to_team"] == i["to_team"] for o in items):
            r.block("two_way", f"{_name(conn, i['upid'])} is a two-way player and has to be traded with {_name(conn, twin)}.",
                    i["from_team"])


def _check_offer_cap(conn, trade, r):
    sender = trade.get("created_by")
    n = _one(conn, """select count(*) as n from trades_live where created_by = %s and status in ('pending', 'accepted')
                         and trade_id is distinct from %s""", (sender, trade.get("trade_id")))["n"]
    if n >= MAX_ACTIVE_OFFERS:
        r.block("offer_cap", f"{sender} already has {n} active offers (the limit is {MAX_ACTIVE_OFFERS}).", sender)


def _raise_if_blocked(res, stage=None):
    if res["blocks"]:
        raise TradeError(" ".join(b["message"] for b in res["blocks"]), issues=res["blocks"])


# ------------------------------------------------------------------ the impact panel

def build_preview(conn, trade, now=None):
    """What the trade does to each team, for the builder, review sheet, inbox and admin queue.

    {"teams": [{"team", "wb": {now, after, out, in, tied_up}, "roster": {now, after, limits, violations},
                "prospects": {PC, DC, BC (after)}, "picks": [...]}],
     "blocks": [...], "warnings": [...]}   (blocks and warnings come from validate at stage 'build')
    """
    ts = _now(now)
    items = [_item(i) for i in trade.get("items") or []]
    season = trade["season"]
    res = validate(conn, trade, "build", ts)
    lim = _roster_limits(conn, season)
    teams = list(trade.get("teams") or sorted({i["from_team"] for i in items} | {i["to_team"] for i in items}))
    panel = []
    for team in teams:
        wb_out = sum(i["amount"] for i in items if i["kind"] == "wb" and i["from_team"] == team)
        wb_in = sum(i["amount"] for i in items if i["kind"] == "wb" and i["to_team"] == team)
        bal = actions.balance(conn, team)
        before, after = _roster_after(conn, items, team)
        violations = [v for v in actions.roster_violations(after, lim) if v["rule"] != "catcher_min"] if lim else []
        # prospect counts after: open prospect contracts, minus those leaving, plus those arriving
        pros = {k: 0 for k in actions.PROSPECT_TYPES}
        for c in _all(conn, "select prospect_type, count(*) as n from contracts where team_abbr = %s and kind = 'prospect' "
                            "and valid_to is null group by 1", (team,)):
            pros[c["prospect_type"]] = c["n"]
        for i in items:
            if i["kind"] == "player" and team in (i["from_team"], i["to_team"]):
                c = _one(conn, "select prospect_type from contracts where upid = %s and valid_to is null and kind = 'prospect'", (i["upid"],))
                if c:
                    pros[c["prospect_type"]] += 1 if i["to_team"] == team else -1
        picks = []
        for i in items:
            if i["kind"] == "pick" and team in (i["from_team"], i["to_team"]):
                s = _slot(conn, i["draft_id"], i["overall"])
                if s:
                    picks.append({"draft_id": s["draft_id"], "overall": s["overall"], "round": s["round"], "label": _pick_label(s),
                                  "original_team": s["original_team"], "direction": "give" if i["from_team"] == team else "get",
                                  "bought_in": {t: bought_in(conn, s["draft_id"], t, s["round"]) for t in (i["from_team"], i["to_team"])}})
        panel.append({
            "team": team,
            "wb": {"now": bal, "after": bal - wb_out + wb_in, "out": wb_out, "in": wb_in,
                   "tied_up": committed_wb(conn, team, trade.get("trade_id"))},
            "roster": {"now": _counts(before), "after": _counts(after), "violations": violations,
                       "limits": {k: lim[k] for k in ("active_max", "sp_max", "sp_max_with_swing", "pitcher_max", "batter_max")} if lim else None},
            "prospects": pros,
            "picks": picks,
        })
    return {"teams": panel, "blocks": res["blocks"], "warnings": res["warnings"]}


# ------------------------------------------------------------------ writing a trade

def _insert_trade(conn, trade, ts, *, status, source, countered_from=None, raw=None, approved_by=None, accepted_all=False):
    """Insert trades_live + parties + items. Returns the new trade_id."""
    tid = _one(conn, "select trade_next_id(%s) as id", (trade["season"],))["id"]
    pending = status == "pending"
    approved = status == "approved"
    _exec(conn, """insert into trades_live (trade_id, season, status, created_by, created_at, expires_at, countered_from,
                                            approved_by, approved_at, source, raw)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
          (tid, trade["season"], status, trade["created_by"], ts, ts + timedelta(days=EXPIRY_DAYS) if pending else None,
           countered_from, approved_by if approved else None, ts if approved else None, source,
           Jsonb(raw) if raw is not None else None))
    for team in trade["teams"]:
        _exec(conn, "insert into trade_parties (trade_id, team_abbr, accepted_at) values (%s, %s, %s)",
              (tid, team, ts if accepted_all or team == trade["created_by"] else None))
    for i in (_item(i) for i in trade["items"]):
        _exec(conn, """insert into trade_items (trade_id, kind, upid, draft_id, overall, amount, from_team, to_team)
                       values (%s, %s, %s, %s, %s, %s, %s, %s)""",
              (tid, i["kind"], i["upid"], i["draft_id"], i["overall"], i["amount"], i["from_team"], i["to_team"]))
    return tid


def _add_comment(conn, trade_id, team, body, ts, kind="comment"):
    body = (body or "").strip()
    if not body:
        raise TradeError("A comment can't be empty.")
    if len(body) > MAX_COMMENT:
        raise TradeError(f"Comments are {MAX_COMMENT} characters at most (this one is {len(body)}).")
    _exec(conn, "insert into trade_comments (trade_id, team_abbr, kind, body, created_at) values (%s, %s, %s, %s, %s)",
          (trade_id, team, kind, body, ts))


def send(conn, trade, now=None, note=None):
    """Validate at 'send', store the offer as pending (the sender counts as accepted), DM the other teams.
    The optional note becomes the first comment. Returns the new trade_id."""
    ts = _now(now)
    with conn.transaction():
        _lock_teams(conn, trade.get("teams") or [])
        _raise_if_blocked(validate(conn, trade, "send", ts))
        tid = _insert_trade(conn, trade, ts, status="pending", source="portal")
        if note and note.strip():
            _add_comment(conn, tid, trade["created_by"], note, ts)
        _dm(conn, tid, "trade_offer", [t for t in trade["teams"] if t != trade["created_by"]], ts, from_team=trade["created_by"])
    return tid


def _open_offer(conn, trade_id, ts, statuses=("pending",)):
    t = _lock_trade(conn, trade_id)
    if t["status"] not in statuses:
        raise TradeError(f"Trade {trade_id} is {t['status']}, so that can't be done.")
    if t["status"] == "pending" and t["expires_at"] is not None and t["expires_at"] <= ts:
        raise TradeError(f"Trade {trade_id} has expired.")
    return t


def _close(conn, trade_id, status, kind, by, note, ts):
    _exec(conn, """update trades_live set status = %s, close_kind = %s, closed_by = %s, close_note = %s, expires_at = null
                    where trade_id = %s""", (status, kind, by, note, trade_id))


def _auto_withdraw(conn, trade_id, issues, parties, ts):
    why = " ".join(b["message"] for b in issues)
    _close(conn, trade_id, "withdrawn", "conflict", "system", why, ts)
    _system_comment(conn, trade_id, f"Withdrawn automatically because the trade can no longer happen: {why}", ts)
    _dm(conn, trade_id, "trade_withdrawn", parties, ts, reason=why, auto=True)
    return TradeConflict(f"Trade {trade_id} was withdrawn automatically: {why}")


def accept(conn, trade_id, team, now=None):
    """A party accepts. The last acceptance moves the trade to 'accepted' (waiting on an admin; it never expires).
    If an item moved since the offer was sent the trade is withdrawn (with a system comment) and TradeConflict is raised."""
    ts = _now(now)
    conflict = None
    with conn.transaction():
        t = _open_offer(conn, trade_id, ts)
        trade = get_trade(conn, trade_id)
        if team not in trade["parties"]:
            raise TradeError(f"{team} is not part of trade {trade_id}.", status=403)
        if trade["parties"][team] is not None:
            raise TradeError(f"{team} already accepted trade {trade_id}.")
        _lock_teams(conn, trade["teams"])
        res = validate(conn, trade, "accept", ts)
        if any(b["code"] == "ownership" for b in res["blocks"]):
            conflict = _auto_withdraw(conn, trade_id, [b for b in res["blocks"] if b["code"] == "ownership"], trade["teams"], ts)
        else:
            _raise_if_blocked(res)
            _exec(conn, "update trade_parties set accepted_at = %s where trade_id = %s and team_abbr = %s", (ts, trade_id, team))
            everyone = _one(conn, "select bool_and(accepted_at is not null) as ok from trade_parties where trade_id = %s", (trade_id,))["ok"]
            others = [x for x in trade["teams"] if x != team]
            if everyone:
                _exec(conn, "update trades_live set status = 'accepted', expires_at = null where trade_id = %s", (trade_id,))
                _notify(conn, "trade_admin_card", None, {"trade_id": trade_id, "audience": "admins"}, ts)
            _dm(conn, trade_id, "trade_accepted", others, ts, by=team, fully_accepted=bool(everyone))
    if conflict:
        raise conflict
    return {"trade_id": trade_id, "status": "accepted" if everyone else "pending"}


def reject(conn, trade_id, by, reason, now=None):
    """A party (not the sender) rejects a pending offer, or an admin rejects an accepted one. A reason is required."""
    ts = _now(now)
    if not (reason and reason.strip()):
        raise TradeError("Rejecting needs a reason.")
    with conn.transaction():
        t = _open_offer(conn, trade_id, ts, ("pending", "accepted"))
        parties = [p["team_abbr"] for p in _all(conn, "select team_abbr from trade_parties where trade_id = %s", (trade_id,))]
        if t["status"] == "pending":
            if by not in parties or by == t["created_by"]:
                raise TradeError("Only the other teams in a pending offer can reject it (the sender withdraws).", status=403)
        else:
            _require_admin(conn, by)
        _close(conn, trade_id, "rejected", "reject", by, reason.strip(), ts)
        _system_comment(conn, trade_id, f"Rejected by {by}: {reason.strip()}", ts)
        _dm(conn, trade_id, "trade_rejected", [p for p in parties if p != by], ts, by=by, reason=reason.strip())
    return {"trade_id": trade_id, "status": "rejected"}


def withdraw(conn, trade_id, team, now=None):
    """The sender pulls a pending offer."""
    ts = _now(now)
    with conn.transaction():
        t = _open_offer(conn, trade_id, ts)
        if team != t["created_by"]:
            raise TradeError("Only the team that sent an offer can withdraw it.", status=403)
        _close(conn, trade_id, "withdrawn", "withdraw", team, None, ts)
        parties = [p["team_abbr"] for p in _all(conn, "select team_abbr from trade_parties where trade_id = %s", (trade_id,))]
        _dm(conn, trade_id, "trade_withdrawn", [p for p in parties if p != team], ts, by=team)
    return {"trade_id": trade_id, "status": "withdrawn"}


def counter(conn, trade_id, team, trade, now=None, note=None):
    """A party answers a pending offer with a new one (`trade`, built from their side). The original becomes
    'countered', the new offer links back with countered_from, and the comment thread follows (see thread())."""
    ts = _now(now)
    with conn.transaction():
        t = _open_offer(conn, trade_id, ts)
        parties = [p["team_abbr"] for p in _all(conn, "select team_abbr from trade_parties where trade_id = %s", (trade_id,))]
        if team not in parties or team == t["created_by"]:
            raise TradeError("Only the other teams in a pending offer can counter it.", status=403)
        if trade.get("created_by") != team:
            raise TradeError("A counter is sent by the team making it.", status=403)
        _lock_teams(conn, list(trade.get("teams") or []) + parties)
        _raise_if_blocked(validate(conn, trade, "send", ts))
        new_id = _insert_trade(conn, trade, ts, status="pending", source="portal", countered_from=trade_id)
        _exec(conn, "update trades_live set status = 'countered', closed_by = %s, expires_at = null where trade_id = %s", (team, trade_id))
        _system_comment(conn, trade_id, f"Countered by {team} with {new_id}.", ts)
        if note and note.strip():
            _add_comment(conn, new_id, team, note, ts)
        _dm(conn, new_id, "trade_countered", [p for p in trade["teams"] if p != team], ts, countered_from=trade_id, from_team=team)
    return new_id


def comment(conn, trade_id, team, body, now=None):
    """A party (or an admin) adds to the thread, before or after approval. DMs the other parties."""
    ts = _now(now)
    with conn.transaction():
        t = _one(conn, "select trade_id from trades_live where trade_id = %s", (trade_id,))
        if not t:
            raise TradeError(f"Trade {trade_id} does not exist.", status=404)
        parties = [p["team_abbr"] for p in _all(conn, "select team_abbr from trade_parties where trade_id = %s", (trade_id,))]
        if team not in parties:
            _require_admin(conn, team)
        _add_comment(conn, trade_id, team, body, ts)
        _dm(conn, trade_id, "trade_comment", [p for p in parties if p != team], ts, by=team)


def expire_pending(conn, now=None):
    """Hourly sweep: pending offers past expires_at become 'expired'. Accepted trades never expire.
    Safe to run twice. Returns the ids it expired."""
    ts = _now(now)
    done = []
    with conn.transaction():
        for r in _all(conn, "select trade_id from trades_live where status = 'pending' and expires_at <= %s order by trade_id", (ts,)):
            t = _lock_trade(conn, r["trade_id"])
            if t["status"] != "pending":
                continue
            _close(conn, t["trade_id"], "expired", "expire", "system", None, ts)
            _system_comment(conn, t["trade_id"], f"Expired after {EXPIRY_DAYS} days without an answer.", ts)
            _dm(conn, t["trade_id"], "trade_expired", [t["created_by"]], ts)
            done.append(t["trade_id"])
    return done


# ------------------------------------------------------------------ approve / manual entry / void

def _apply(conn, trade, by, ts):
    """Spec steps 3-6: players, picks, WizBucks, with their events. Raises (and the caller's transaction
    undoes everything) on any problem."""
    tid, season, items = trade["trade_id"], trade["season"], [_item(i) for i in trade["items"]]
    phase = actions._current_phase(conn, season, ts)
    players = [i for i in items if i["kind"] == "player"]
    for i in players:
        # Until the cutover a player may be on a roster_assignments row only; league.actions.transfer needs a contract.
        if not _player_owner(conn, i["upid"])["has_contract"]:
            raise TradeError(f"{_name(conn, i['upid'])} has no contract row yet (rosters are still the shadow mirror), so he can't be traded.")
    for i in players:
        actions.transfer(conn, i["upid"], i["from_team"], i["to_team"], created_by=by, season=season, ref_id=tid, now=ts)
    for i in items:
        if i["kind"] == "pick":
            n = _exec(conn, "update draft_slots set team_abbr = %s where draft_id = %s and overall = %s and team_abbr = %s",
                      (i["to_team"], i["draft_id"], i["overall"], i["from_team"]))
            if n != 1:
                raise TradeError(f"Pick {i['draft_id']} #{i['overall']} is no longer on {i['from_team']}.")
            _exec(conn, "insert into draft_events (draft_id, kind, actor, detail, at) values (%s, 'trade_pick', %s, %s, %s)",
                  (i["draft_id"], by, Jsonb({"trade_id": tid, "overall": i["overall"], "from": i["from_team"], "to": i["to_team"]}), ts))
    for i in items:
        if i["kind"] == "wb":
            note = f"Trade {tid}: ${i['amount']} {i['from_team']} to {i['to_team']}"
            actions.wb_spend(conn, i["from_team"], i["amount"], "trade_out", phase, season, created_by=by,
                             ref_type="trade", ref_id=tid, note=note)
            actions.wb_credit(conn, i["to_team"], i["amount"], "trade_in", phase, season, created_by=by,
                              ref_type="trade", ref_id=tid, note=note)


def receives(conn, trade_id):
    """{team: [lines]} in the Constitution's format: 'SP Name [MLB] [TC 1]', 'Keeper Pick R6 P66 via CFL', '$15 WB via CFL'."""
    t = get_trade(conn, trade_id)
    out = {team: [] for team in t["teams"]}
    for i in t["items"]:
        if i["kind"] == "player":
            p = _one(conn, """select p.name, p.mlb_team, coalesce(p.mlb_primary_position, split_part(p.positions, ',', 1)) as pos,
                                     c.kind, c.tier, c.prospect_type
                                from players p left join lateral (select * from contracts c where c.upid = p.upid
                                     order by (c.valid_to is null) desc, c.valid_from desc limit 1) c on true
                               where p.upid = %s""", (i["upid"],))
            label = p["prospect_type"] if p["kind"] == "prospect" else (p["tier"] or "").replace("-", " ")
            out[i["to_team"]].append(f"{p['pos'] or '?'} {p['name']} [{p['mlb_team'] or 'FA'}]" + (f" [{label}]" if label else ""))
        elif i["kind"] == "pick":
            s = _slot(conn, i["draft_id"], i["overall"])
            out[i["to_team"]].append(f"Keeper Pick {_pick_label(s)} via {i['from_team']}")
        else:
            out[i["to_team"]].append(f"${i['amount']} WB via {i['from_team']}")
    return out


def _day(ts):
    return ts.astimezone(ET).strftime("%a %-m/%-d").upper()


def _post_text(conn, trade_id, headline):
    t = get_trade(conn, trade_id)
    blocks = [f"🔁 **{team} receives:**\n" + ("\n".join(lines) if lines else "Nothing") for team, lines in receives(conn, trade_id).items()]
    return ("\n\n".join(blocks) + f"\n\n📆 Sub: {_day(t['created_at'])}\n📆 Proc: {_day(t['approved_at'])}\n\n"
            f"{headline} ({trade_id}, approved by {t['approved_by']})")


def _announce_approved(conn, trade_id, ts):
    t = get_trade(conn, trade_id)
    text = _post_text(conn, trade_id, "✅ **TRADE APPROVED**")
    _dm(conn, trade_id, "trade_approved", t["teams"], ts, approved_by=t["approved_by"])
    _notify(conn, "trade_post", None, {"trade_id": trade_id, "channel": "trades", "text": text}, ts)


def approve(conn, trade_id, admin, now=None):
    """Apply an accepted trade in ONE transaction (spec "Approval"). Running it twice is harmless: an
    approved trade is returned as it is. Any block rolls everything back and raises TradeError; an ownership
    conflict withdraws the trade (committed) and raises TradeConflict. Admin only (NotAdmin otherwise)."""
    ts = _now(now)
    _require_admin(conn, admin)
    conflict = None
    with conn.transaction():
        t = _lock_trade(conn, trade_id)
        if t["status"] == "approved":
            return {"trade_id": trade_id, "status": "approved", "already_approved": True}
        if t["status"] != "accepted":
            raise TradeError(f"Trade {trade_id} is {t['status']}; only an accepted trade can be approved.")
        trade = get_trade(conn, trade_id)
        _lock_teams(conn, trade["teams"])
        res = validate(conn, trade, "approve", ts)
        if any(b["code"] == "ownership" for b in res["blocks"]):
            conflict = _auto_withdraw(conn, trade_id, [b for b in res["blocks"] if b["code"] == "ownership"], trade["teams"], ts)
        else:
            _raise_if_blocked(res)
            _apply(conn, trade, admin, ts)
            _exec(conn, "update trades_live set status = 'approved', approved_by = %s, approved_at = %s where trade_id = %s",
                  (admin, ts, trade_id))
            _announce_approved(conn, trade_id, ts)
    if conflict:
        raise conflict
    return {"trade_id": trade_id, "status": "approved", "already_approved": False, "warnings": res["warnings"]}


def admin_manual_entry(conn, trade, admin, note, now=None):
    """Record a deal made outside the portal as an already-approved trade (source 'admin_manual'): the same
    validation (stage 'approve') and side effects as approve. `trade` needs created_by (any team in it). A note is required."""
    ts = _now(now)
    _require_admin(conn, admin)
    if not (note and note.strip()):
        raise TradeError("A manual trade needs a note saying why it was entered by hand.")
    with conn.transaction():
        _lock_teams(conn, trade.get("teams") or [])
        res = validate(conn, trade, "send", ts)       # structure + the approve rules; the offer cap does not apply to an admin entry
        res["blocks"] = [b for b in res["blocks"] if b["code"] != "offer_cap"]
        _raise_if_blocked(res)
        tid = _insert_trade(conn, trade, ts, status="approved", source="admin_manual", approved_by=admin,
                            accepted_all=True, raw={"note": note.strip(), "entered_by": admin})
        _system_comment(conn, tid, f"Entered by hand by {admin}: {note.strip()}", ts)
        _apply(conn, get_trade(conn, tid), admin, ts)
        _announce_approved(conn, tid, ts)
    return {"trade_id": tid, "status": "approved", "warnings": res["warnings"]}


def void(conn, trade_id, admin, reason, now=None):
    """Reverse an approved trade in ONE transaction (the Constitution's injury-void rule). Players go back
    through league.actions.transfer, picks back to their old owner, and every WizBucks ledger row of the trade
    gets a 'reversal' row (the ledger is append-only). Fails, changing nothing, if anything has moved since
    (a player traded on, a pick used, or WizBucks the other team can no longer give back). Admin only."""
    ts = _now(now)
    _require_admin(conn, admin)
    if not (reason and reason.strip()):
        raise TradeError("Voiding a trade needs a reason.")
    reason = reason.strip()
    with conn.transaction():
        t = _lock_trade(conn, trade_id)
        if t["status"] != "approved":
            raise TradeError(f"Trade {trade_id} is {t['status']}; only an approved trade can be voided.")
        trade = get_trade(conn, trade_id)
        _lock_teams(conn, trade["teams"])
        season = trade["season"]
        items = [_item(i) for i in trade["items"]]
        # money first: reversal credits (undoing the trade_out rows) before reversal debits, so a team that
        # both gave and got WizBucks nets out
        for row in _all(conn, """select id, team_abbr, amount, phase, season from wb_ledger
                                  where ref_type = 'trade' and ref_id = %s and kind in ('trade_out', 'trade_in')
                                  order by amount, id""", (trade_id,)):
            actions.admin_adjust(conn, reason=f"Void of {trade_id}: {reason}", created_by=admin, season=row["season"],
                                 phase=row["phase"], team=row["team_abbr"], amount=-row["amount"], kind="reversal",
                                 reverses_ledger_id=row["id"], now=ts)
        for i in items:
            if i["kind"] == "player":
                actions.transfer(conn, i["upid"], i["to_team"], i["from_team"], created_by=admin, season=season,
                                 ref_id=f"void of {trade_id}", now=ts)
                actions._event(conn, i["upid"], "trade_void", f"{_name(conn, i['upid'])} returned to {i['from_team']}: {trade_id} voided by {admin} ({reason}).",
                               team=i["from_team"], season=season, by=admin, ts=ts)
            elif i["kind"] == "pick":
                s = _slot(conn, i["draft_id"], i["overall"])
                if s["used"]:
                    raise TradeError(f"{_pick_label(s)} has already been used, so the trade can't be voided.")
                if _exec(conn, "update draft_slots set team_abbr = %s where draft_id = %s and overall = %s and team_abbr = %s",
                         (i["from_team"], i["draft_id"], i["overall"], i["to_team"])) != 1:
                    raise TradeError(f"{_pick_label(s)} is no longer on {i['to_team']}, so the trade can't be voided.")
                _exec(conn, "insert into draft_events (draft_id, kind, actor, detail, at) values (%s, 'trade_pick_void', %s, %s, %s)",
                      (i["draft_id"], admin, Jsonb({"trade_id": trade_id, "overall": i["overall"], "from": i["to_team"], "to": i["from_team"]}), ts))
        _exec(conn, "update trades_live set status = 'voided', close_kind = 'void', closed_by = %s, close_note = %s where trade_id = %s",
              (admin, reason, trade_id))
        _system_comment(conn, trade_id, f"Voided by {admin}: {reason}", ts)
        _dm(conn, trade_id, "trade_voided", trade["teams"], ts, by=admin, reason=reason)
        _notify(conn, "trade_post", None, {"trade_id": trade_id, "channel": "trades",
                                           "text": f"❌ **TRADE VOIDED** ({trade_id}) by {admin}: {reason}"}, ts)
    return {"trade_id": trade_id, "status": "voided"}


def admin_queue(conn, now=None):
    """Accepted trades waiting on an admin, oldest first, each with its impact panel (roster warnings included)."""
    out = []
    for r in _all(conn, "select trade_id from trades_live where status = 'accepted' order by created_at, trade_id"):
        t = get_trade(conn, r["trade_id"])
        out.append({"trade": t, "preview": build_preview(conn, t, now)})
    return out
