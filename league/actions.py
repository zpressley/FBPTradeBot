"""League actions for Spec 03 (rosters, contracts, WizBucks).

One function per row of the spec's Actions table. Each takes a psycopg
connection and plain arguments, runs in one transaction (the caller opens it
with `with conn.transaction():`; every action also wraps itself in a nested
transaction so a RuleError undoes the action's partial writes), writes
`player_events` and, when money moves, `wb_ledger`, and returns a small dict
with a plain-language `message`.

Rules are data: tier math comes from `contract_tiers`, prices from
`wb_prices`, limits from `roster_limits` / `prospect_limits`. Nothing in this
file hardcodes a price, a tier name or a limit. Every function that touches
a team's money or roster takes `pg_advisory_xact_lock(hashtext('team:' || abbr))`
first (the SQL wb_spend/wb_credit functions take their own `wb:` lock after).

Errors are raised as RuleError(message).
"""

from __future__ import annotations

import functools
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

ET = ZoneInfo("America/New_York")
EVENT_SOURCE = "league_actions"
PROSPECT_TYPES = ("DC", "PC", "BC")
SLOTS = ("active", "il", "na", "farm")
DEBUT_FEE_WINDOW_DAYS = 7   # Constitution Art. III s02: "within 7 days" (a rule constant, see report)
FEE_EXEMPT_MONTH = 9        # September call-ups are exempt from the debut fee
IL_INELIGIBLE_DAYS = 35     # an IL-tagged player can't play for 5 weeks after the KAP ends

# Ledger kinds the low-level wrappers will write. penalty() and admin_adjust() are the only
# paths for 'penalty', 'admin_adjustment' and 'reversal'.
SPEND_KINDS = ("prospect_contract", "prospect_upgrade", "debut_fee", "draft_slot", "buyin", "rat",
               "keeper_salary", "auction_win", "trade_out", "rollover_forfeit")
CREDIT_KINDS = ("installment", "finish_reward", "rollover_in", "buyin_refund", "trade_in",
                "penalty_credit", "draft_award", "opening_balance")
WINDOWS = ("PAD", "KAP", "BC_FINALIZATION", "TRADE")


class RuleError(Exception):
    """A league rule stopped the action. The message is safe to show a manager."""


# ------------------------------------------------------------------ roster-change hooks

_ROSTER_HOOKS = []


def on_roster_change(callback):
    """Register callback(conn, team, upids, now), run inside the action's transaction whenever players
    leave a team's active slot (trade, release, move out of active, graduation). lineups.service registers
    its scrub_lineups here at import; league.actions never imports lineups at module level (that would be a cycle)."""
    if callback not in _ROSTER_HOOKS:
        _ROSTER_HOOKS.append(callback)
    return callback


def _roster_changed(conn, team, upids, ts):
    try:
        import lineups.service  # noqa: F401  late import: makes sure the lineup hook is registered
    except ImportError:
        pass
    for cb in list(_ROSTER_HOOKS):
        cb(conn, team, list(upids), ts)


# ------------------------------------------------------------------ plumbing

def _atomic(fn):
    @functools.wraps(fn)
    def wrapper(conn, *args, **kwargs):
        with conn.transaction():
            return fn(conn, *args, **kwargs)

    return wrapper


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


def _lock(conn, *teams):
    for t in sorted({t for t in teams if t}):
        _exec(conn, "select pg_advisory_xact_lock(hashtext('team:' || %s))", (t,))


def _season(conn, season):
    if season is not None:
        return season
    row = _one(conn, "select max(season) as s from roster_limits")
    if not row or row["s"] is None:
        raise RuleError("no season configured in roster_limits")
    return row["s"]


def _team(conn, abbr):
    t = _one(conn, "select abbr, name, active from teams where abbr = %s", (abbr,))
    if not t:
        raise RuleError(f"unknown team {abbr!r}")
    return t


def _player(conn, upid):
    p = _one(conn, "select upid, name, positions, mlb_primary_position, carried_tier from players where upid = %s", (upid,))
    if not p:
        raise RuleError(f"unknown player {upid!r}")
    return p


def _price(conn, season, item):
    r = _one(conn, "select amount from wb_prices where season = %s and item = %s", (season, item))
    if not r:
        raise RuleError(f"no price for {item!r} in season {season}")
    return r["amount"]


def _tier(conn, tier):
    t = _one(conn, "select * from contract_tiers where tier = %s", (tier,))
    if not t:
        raise RuleError(f"unknown contract tier {tier!r}")
    return t


def _entry_tier(conn, entry):
    r = _one(conn, "select tier from contract_entry_tiers where entry = %s", (entry,))
    if not r:
        raise RuleError(f"no entry tier configured for {entry!r}")
    return r["tier"]


def balance(conn, team) -> int:
    return _one(conn, "select coalesce(sum(amount), 0)::int as b from wb_ledger where team_abbr = %s", (team,))["b"]


def _event(conn, upid, update_type, text, *, team, season, by, ts, contract=None):
    player = _one(conn, "select name from players where upid = %s", (upid,)) if upid else None
    _exec(
        conn,
        """insert into player_events (id, season, ts, update_type, event, source, admin, upid, player_name,
                                      owner_abbr, contract, source_file, id_generated)
           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false)""",
        (str(uuid.uuid4()), season, ts, update_type, text, EVENT_SOURCE, by, upid,
         player["name"] if player else None, team, contract, EVENT_SOURCE),
    )


def _spend(conn, team, amount, kind, phase, season, *, upid=None, ref_type=None, ref_id=None, note=None, by):
    """Call the SQL wb_spend inside a savepoint so an 'insufficient WizBucks' error is a RuleError."""
    try:
        with conn.transaction():
            row = _one(conn, "select wb_spend(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) as id",
                       (team, amount, kind, phase, season, upid, ref_type, ref_id, note, by))
    except psycopg.errors.RaiseException as e:
        raise RuleError(e.diag.message_primary) from e
    return row["id"]


def _credit(conn, team, amount, kind, phase, season, *, upid=None, ref_type=None, ref_id=None, note=None, by):
    try:
        with conn.transaction():
            row = _one(conn, "select wb_credit(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) as id",
                       (team, amount, kind, phase, season, upid, ref_type, ref_id, note, by))
    except psycopg.errors.RaiseException as e:
        raise RuleError(e.diag.message_primary) from e
    return row["id"]


def _locked_contract(conn, upid, *, team=None, kind=None):
    """The player's open contract, with his team locked and the row locked FOR UPDATE."""
    c = _one(conn, "select team_abbr from contracts where upid = %s and valid_to is null", (upid,))
    if not c:
        raise RuleError(f"{_player(conn, upid)['name']} has no open contract")
    _lock(conn, c["team_abbr"])
    c = _one(conn, "select * from contracts where upid = %s and valid_to is null for update", (upid,))
    if not c:
        raise RuleError("the contract changed while waiting for the team lock; try again")
    if team is not None and c["team_abbr"] != team:
        raise RuleError(f"{_player(conn, upid)['name']} is not on {team}")
    if kind is not None and c["kind"] != kind:
        raise RuleError(f"{_player(conn, upid)['name']} is on a {c['kind']} contract, not a {kind} contract")
    return c


def _set_slot(conn, upid, slot, ts, season=None):
    """Put a player in a roster slot. The only writer of roster_slots after the cutover, so the IL and NA
    maximums (roster_limits.il_max / na_max, outside the 26) are enforced here for every path."""
    if slot in ("il", "na"):
        c = _one(conn, "select team_abbr from contracts where upid = %s and valid_to is null", (upid,))
        lim = _limits(conn, _season(conn, season))
        cap = lim[f"{slot}_max"]
        if c:
            n = _one(conn, """select count(*) as n from contracts c join roster_slots rs on rs.upid = c.upid and rs.slot = %s
                               where c.team_abbr = %s and c.valid_to is null and c.upid <> %s""",
                     (slot, c["team_abbr"], upid))["n"]
            if n >= cap:
                raise RuleError(f"{c['team_abbr']} already has {n} on {slot.upper()} (max {cap}).")
    _exec(
        conn,
        """insert into roster_slots (upid, slot, updated_at) values (%s, %s, %s)
           on conflict (upid) do update set slot = excluded.slot, updated_at = excluded.updated_at""",
        (upid, slot, ts),
    )


# ------------------------------------------------------------------ phase windows

def window_is_open(conn, season, window, ts) -> bool:
    return _one(conn, """select 1 as x from league_windows
                          where season = %s and window_name = %s and opens_at <= %s and %s < closes_at""",
                (season, window, ts, ts)) is not None


def _require_window(conn, season, window, ts, what):
    """Phase gating comes from the league_windows table, never from an argument the caller passes.

    There is deliberately no bypass for created_by='system': the one automatic job the spec runs at
    KAP close (release of non-kept players) is not a gated action.
    """
    if not window_is_open(conn, season, window, ts):
        raise RuleError(f"{what} is only allowed while the {window} window is open, and it is not open now")


def _current_phase(conn, season, ts):
    """Ledger phase for an action that is allowed at several times, taken from the open windows."""
    for w in ("PAD", "KAP"):
        if window_is_open(conn, season, w, ts):
            return w
    return "PAD" if window_is_open(conn, season, "BC_FINALIZATION", ts) else "IN_SEASON"


@_atomic
def open_window(conn, season, window, opens_at, closes_at, by):
    """Admin: open (or re-time) a league window."""
    if window not in WINDOWS:
        raise RuleError(f"window must be one of {WINDOWS}")
    if not by or by == "system":
        raise RuleError("a window is opened by a named admin")
    if closes_at <= opens_at:
        raise RuleError("a window must close after it opens")
    _exec(conn, """insert into league_windows (season, window_name, opens_at, closes_at, opened_by)
                   values (%s, %s, %s, %s, %s)
                   on conflict (season, window_name) do update
                     set opens_at = excluded.opens_at, closes_at = excluded.closes_at, opened_by = excluded.opened_by""",
          (season, window, opens_at, closes_at, by))
    msg = f"{window} window for {season} open from {opens_at} to {closes_at} (by {by})."
    _event(conn, None, "window_open", msg, team=None, season=season, by=by, ts=_now(None))
    return {"message": msg}


@_atomic
def close_window(conn, season, window, by, now=None):
    """Admin: close a window now. A window that never opened is removed."""
    ts = _now(now)
    row = _one(conn, "select opens_at, closes_at from league_windows where season = %s and window_name = %s for update",
               (season, window))
    if not row:
        raise RuleError(f"no {window} window for {season}")
    if ts <= row["opens_at"]:
        _exec(conn, "delete from league_windows where season = %s and window_name = %s", (season, window))
    elif ts < row["closes_at"]:
        _exec(conn, "update league_windows set closes_at = %s where season = %s and window_name = %s", (ts, season, window))
    msg = f"{window} window for {season} closed (by {by})."
    _event(conn, None, "window_close", msg, team=None, season=season, by=by, ts=ts)
    return {"message": msg}


def _open_contract(conn, **c):
    cols = ["upid", "team_abbr", "kind", "tier", "prospect_type", "acquired_via", "acquired_round", "il_tag",
            "il_tag_season", "il_tag_family", "il_tag_team", "il_ineligible_until", "bc_decision",
            "bc_decision_season", "rat_count", "international_rookie", "valid_from"]
    vals = [c.get(k) for k in cols]
    for i, k in enumerate(cols):
        if vals[i] is None and k in ("il_tag", "rat_count", "international_rookie"):
            vals[i] = {"il_tag": False, "rat_count": 0, "international_rookie": False}[k]
    row = _one(conn, f"insert into contracts ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) returning id", vals)
    _exec(conn, "update players set carried_tier = null where upid = %s", (c["upid"],))
    return row["id"]


def _close_contract(conn, contract_id, reason, ts):
    _exec(conn, "update contracts set valid_to = %s, end_reason = %s where id = %s", (ts, reason, contract_id))


# --------------------------------------------------------------- roster limits

_PITCHER_TOKENS = {"SP", "RP", "P", "RHP", "LHP"}


def _tokens(positions):
    return {t.strip().upper() for t in (positions or "").replace("/", ",").split(",") if t.strip()}


def classify(positions, primary=None, eligibility=None) -> dict:
    """pitcher / sp / swing (both SP and RP eligible) / catcher flags.

    `eligibility` is the player's set of position_eligibility positions for the season (Spec 07: the roster
    limits read the same table the lineup does). When it is empty or None the player has no eligibility rows
    and the players.positions text is used instead: pre-cutover behaviour, before the nightly eligibility
    job has filled the table."""
    if eligibility:
        toks = {t.upper() for t in eligibility}
    else:
        toks = _tokens(positions)
        if not toks and primary:
            toks = {primary.strip().upper()}
    pitcher = bool(toks & _PITCHER_TOKENS)
    return {
        "pitcher": pitcher,
        "sp": "SP" in toks,
        "swing": "SP" in toks and "RP" in toks,
        "catcher": "C" in toks,
    }


def _limits(conn, season):
    lim = _one(conn, "select * from roster_limits where season = %s", (season,))
    if not lim:
        raise RuleError(f"no roster limits for season {season}")
    return lim


def _eligibility_sets(conn, upids, season):
    """{upid: set of position_eligibility positions for the season}; a player with no rows is left out."""
    out = {}
    for r in _all(conn, "select upid, position from position_eligibility where season = %s and upid = any(%s)",
                  (season, list(upids))):
        out.setdefault(r["upid"], set()).add(r["position"])
    return out


def _with_eligibility(conn, players, season):
    """Add an 'eligibility' set to each player dict (None when he has no rows: classify falls back to text)."""
    elig = _eligibility_sets(conn, [p["upid"] for p in players], season)
    return [{**p, "eligibility": elig.get(p["upid"])} for p in players]


def _active_players(conn, team, season=None):
    players = _all(
        conn,
        """select p.upid, p.name, p.positions, p.mlb_primary_position
             from contracts c
             join roster_slots rs on rs.upid = c.upid and rs.slot = 'active'
             join players p on p.upid = c.upid
            where c.team_abbr = %s and c.valid_to is null""",
        (team,),
    )
    return _with_eligibility(conn, players, _season(conn, season))


def roster_violations(players, lim) -> list[dict]:
    """Compare a list of active players with a roster_limits row. Pure function."""
    cl = [classify(p["positions"], p["mlb_primary_position"], p.get("eligibility")) for p in players]
    n = len(players)
    sp = sum(c["sp"] for c in cl)
    pitchers = sum(c["pitcher"] for c in cl)
    batters = n - pitchers
    catchers = sum(c["catcher"] for c in cl)
    swing = any(c["swing"] for c in cl)
    sp_cap = lim["sp_max_with_swing"] if swing else lim["sp_max"]
    out = []
    if n > lim["active_max"]:
        out.append({"rule": "active_max", "over": n - lim["active_max"], "detail": f"{n} active, max {lim['active_max']}"})
    if sp > sp_cap:
        out.append({"rule": "sp_max", "over": sp - sp_cap, "detail": f"{sp} SP, max {sp_cap}" + ("" if swing else " (no SP/RP swing pitcher)")})
    if pitchers > lim["pitcher_max"]:
        out.append({"rule": "pitcher_max", "over": pitchers - lim["pitcher_max"], "detail": f"{pitchers} pitchers, max {lim['pitcher_max']}"})
    if batters > lim["batter_max"]:
        out.append({"rule": "batter_max", "over": batters - lim["batter_max"], "detail": f"{batters} batters, max {lim['batter_max']}"})
    if catchers < lim["catcher_min"]:
        out.append({"rule": "catcher_min", "over": lim["catcher_min"] - catchers, "detail": f"{catchers} catchers, min {lim['catcher_min']}"})
    return out


def _check_can_activate(conn, team, upid, season):
    """Raise RuleError if putting `upid` in the active slot would break a roster maximum."""
    lim = _limits(conn, season)
    current = [p for p in _active_players(conn, team, season) if p["upid"] != upid]
    before = {v["rule"]: v["over"] for v in roster_violations(current, lim)}
    new = _with_eligibility(conn, [_player(conn, upid)], season)[0]
    after = roster_violations(current + [new], lim)
    # catcher_min is a detection-only rule (you may need to add a catcher to fix it)
    bad = [v for v in after if v["rule"] != "catcher_min" and v["over"] > before.get(v["rule"], 0)]
    if bad:
        raise RuleError(f"{new['name']} can't go active on {team}: " + "; ".join(v["detail"] for v in bad))


@_atomic
def roster_check(conn, *, season=None, team=None, now=None):
    """Nightly in-season check. Returns violations with the penalty pre-filled; charges nothing.

    An admin confirms each one through `penalty()`.
    """
    season = _season(conn, season)
    lim = _limits(conn, season)
    first = _price(conn, season, "roster_violation")
    teams = [team] if team else [t["abbr"] for t in _all(conn, "select abbr from teams where active order by abbr")]
    found = []
    for abbr in teams:
        for v in roster_violations(_active_players(conn, abbr, season), lim):
            found.append({"team": abbr, **v, "penalty_amount": first})
    return {"violations": found, "message": f"{len(found)} roster violation(s) found; nothing charged until an admin confirms."}


# ---------------------------------------------------------------- prospects

def _slot_type_for_round(conn, season, draft_round):
    r = _one(conn, "select bc_slot_max_round from prospect_limits where season = %s", (season,))
    if not r:
        raise RuleError(f"no prospect limits for season {season}")
    return "BC" if draft_round <= r["bc_slot_max_round"] else "DC"


@_atomic
def assign_prospect_contract(conn, upid, team, prospect_type=None, *, acquired_via, created_by, season=None,
                             draft_round=None, international_rookie=False, now=None):
    """Open a DC/PC/BC contract.

    acquired_via 'pad': needs an open PAD window and charges the price. 'prospect_draft': fills the team's next open slot of the
    right type (BC for the early rounds, DC after) with no charge. 'auction': PC, already paid.
    """
    ts, season = _now(now), _season(conn, season)
    _team(conn, team)
    p = _player(conn, upid)
    _lock(conn, team)
    if acquired_via not in ("pad", "prospect_draft", "auction"):
        raise RuleError(f"a prospect contract can't be assigned via {acquired_via!r}")
    if _one(conn, "select 1 as x from contracts where upid = %s and valid_to is null", (upid,)):
        raise RuleError(f"{p['name']} already has an open contract")
    charged = 0
    if acquired_via == "prospect_draft":
        if draft_round is None:
            raise RuleError("draft_round is required for a prospect draft pick")
        slot_type = _slot_type_for_round(conn, season, draft_round)
        if prospect_type not in (None, slot_type):
            raise RuleError(f"round {draft_round} picks fill {slot_type} slots, not {prospect_type}")
        prospect_type = slot_type
        slot = _one(conn, """select id from prospect_draft_slots
                              where season = %s and team_abbr = %s and slot_type = %s and filled_upid is null
                              order by id limit 1 for update skip locked""", (season, team, slot_type))
        if not slot:
            raise RuleError(f"{team} has no open {slot_type} draft slot for round {draft_round}")
        _exec(conn, "update prospect_draft_slots set filled_upid = %s, filled_at = %s where id = %s", (upid, ts, slot["id"]))
    elif acquired_via == "auction":
        if prospect_type not in (None, "PC"):
            raise RuleError("auction wins are Purchased Contracts (PC)")
        prospect_type = "PC"
    else:
        if prospect_type not in PROSPECT_TYPES:
            raise RuleError(f"prospect_type must be one of {PROSPECT_TYPES}")
        _require_window(conn, season, "PAD", ts, "buying a prospect contract")
        charged = _price(conn, season, prospect_type.lower())
        _spend(conn, team, charged, "prospect_contract", "PAD", season, upid=upid, ref_type="pad",
               ref_id=str(season), note=f"{prospect_type} contract for {p['name']}", by=created_by)
    cid = _open_contract(conn, upid=upid, team_abbr=team, kind="prospect", prospect_type=prospect_type,
                         acquired_via=acquired_via, acquired_round=draft_round,
                         international_rookie=international_rookie, valid_from=ts)
    _set_slot(conn, upid, "farm", ts)
    msg = f"{team} signed {p['name']} to a {prospect_type} contract" + (f" for ${charged}." if charged else " (already paid).")
    _event(conn, upid, "prospect_contract", msg, team=team, season=season, by=created_by, ts=ts, contract=prospect_type)
    return {"contract_id": cid, "prospect_type": prospect_type, "charged": charged, "message": msg}


@_atomic
def buy_draft_slot(conn, team, slot_type, *, created_by, season=None, count=1, now=None):
    """Pay the BC or DC contract price now and get an empty prospect contract to fill at the Prospect Draft."""
    ts, season = _ts_season(conn, now, season)
    _team(conn, team)
    _require_window(conn, season, "PAD", ts, "buying a draft slot")
    if slot_type not in ("BC", "DC"):
        raise RuleError("slot_type must be 'BC' or 'DC'")
    if count < 1:
        raise RuleError("count must be at least 1")
    _lock(conn, team)
    if slot_type == "DC":
        cap = _limits(conn, season)["dc_slot_max"]
        have = _one(conn, "select count(*) as n from prospect_draft_slots where season = %s and team_abbr = %s and slot_type = 'DC'",
                    (season, team))["n"]
        if have + count > cap:
            raise RuleError(f"{team} can hold at most {cap} DC slots; has {have}, asked for {count} more")
    price = _price(conn, season, slot_type.lower())
    ids = []
    for _ in range(count):
        lid = _spend(conn, team, price, "draft_slot", "PAD", season, ref_type="draft", ref_id=str(season),
                     note=f"{slot_type} draft slot", by=created_by)
        ids.append(_one(conn, """insert into prospect_draft_slots (season, team_abbr, slot_type, ledger_id)
                                 values (%s, %s, %s, %s) returning id""", (season, team, slot_type, lid))["id"])
    msg = f"{team} bought {count} {slot_type} draft slot(s) for ${price * count}."
    _event(conn, None, "draft_slot_purchase", msg, team=team, season=season, by=created_by, ts=ts)
    return {"slot_ids": ids, "charged": price * count, "message": msg}


def _ts_season(conn, now, season):
    return _now(now), _season(conn, season)


@_atomic
def upgrade_prospect(conn, upid, to_type, *, created_by, season=None, team=None, now=None):
    """DC->PC any time. DC->BC and PC->BC only while the admin-run BC_FINALIZATION window is open."""
    ts, season = _ts_season(conn, now, season)
    c = _locked_contract(conn, upid, team=team, kind="prospect")
    frm = c["prospect_type"]
    item = {("DC", "PC"): "dc_to_pc", ("DC", "BC"): "dc_to_bc", ("PC", "BC"): "pc_to_bc"}.get((frm, to_type))
    if item is None:
        raise RuleError(f"can't upgrade a {frm} contract to {to_type}")
    if to_type == "BC" and not window_is_open(conn, season, "BC_FINALIZATION", ts):
        raise RuleError("Blue Chip upgrades are only allowed during post-draft finalization, which an admin opens")
    price = _price(conn, season, item)
    name = _player(conn, upid)["name"]
    _spend(conn, c["team_abbr"], price, "prospect_upgrade", _current_phase(conn, season, ts), season, upid=upid, ref_type="upgrade",
           ref_id=f"{frm}>{to_type}", note=f"{frm} to {to_type} for {name}", by=created_by)
    _exec(conn, "update contracts set prospect_type = %s where id = %s", (to_type, c["id"]))
    msg = f"{name} upgraded from {frm} to {to_type} for ${price}."
    _event(conn, upid, "prospect_upgrade", msg, team=c["team_abbr"], season=season, by=created_by, ts=ts, contract=to_type)
    return {"charged": price, "prospect_type": to_type, "message": msg}


def _debut_state(conn, upid, ts):
    st = _one(conn, "select mlb_debut from prospect_status where upid = %s", (upid,))
    if not st or st["mlb_debut"] is None:
        raise RuleError("no MLB debut recorded for this player")
    debut: date = st["mlb_debut"]
    deadline = debut + timedelta(days=DEBUT_FEE_WINDOW_DAYS)
    today = ts.astimezone(ET).date()
    return debut, deadline, today


@_atomic
def pay_debut_fee(conn, upid, *, created_by, season=None, team=None, now=None):
    """$15 within 7 days of a DC prospect's MLB debut; the DC becomes a PC."""
    ts, season = _ts_season(conn, now, season)
    c = _locked_contract(conn, upid, team=team, kind="prospect")
    if c["prospect_type"] != "DC":
        raise RuleError("only Development Contract prospects owe the debut fee")
    debut, deadline, today = _debut_state(conn, upid, ts)
    name = _player(conn, upid)["name"]
    if debut.month == FEE_EXEMPT_MONTH:
        raise RuleError(f"{name} debuted in September, so there is no fee and no void; "
                        "he can't be rostered unless purchased (upgrade to PC)")
    if today > deadline:
        raise RuleError(f"the debut fee window closed on {deadline}; the contract is voided")
    fee = _price(conn, season, "debut_fee")
    _spend(conn, c["team_abbr"], fee, "debut_fee", "IN_SEASON", season, upid=upid, ref_type="debut",
           ref_id=str(debut), note=f"debut fee for {name}", by=created_by)
    _exec(conn, "update contracts set prospect_type = 'PC' where id = %s", (c["id"],))
    msg = f"{name} debuted {debut}; ${fee} debut fee paid and his DC is now a PC."
    _event(conn, upid, "debut_fee", msg, team=c["team_abbr"], season=season, by=created_by, ts=ts, contract="PC")
    return {"charged": fee, "message": msg}


@_atomic
def void_dc(conn, upid, *, created_by="system", season=None, now=None):
    """Void a DC whose owner didn't pay within 7 days of the debut. The player goes to the next auction pool."""
    ts, season = _ts_season(conn, now, season)
    c = _locked_contract(conn, upid, kind="prospect")
    if c["prospect_type"] != "DC":
        raise RuleError("only Development Contracts are voided for a missed debut fee")
    debut, deadline, today = _debut_state(conn, upid, ts)
    name = _player(conn, upid)["name"]
    if debut.month == FEE_EXEMPT_MONTH:
        raise RuleError(f"{name} debuted in September: no fee, no void")
    if today <= deadline:
        raise RuleError(f"{name}'s fee window is open until {deadline}")
    _close_contract(conn, c["id"], "voided", ts)
    _exec(conn, "delete from roster_slots where upid = %s", (upid,))
    msg = f"{c['team_abbr']} didn't pay the debut fee for {name}; his DC is void and he goes to the next Auction Portal."
    _event(conn, upid, "dc_voided", msg, team=c["team_abbr"], season=season, by=created_by, ts=ts, contract="DC")
    return {"voided": True, "to_auction_pool": True, "message": msg}


@_atomic
def void_overdue_dcs(conn, *, created_by="system", season=None, now=None):
    """Job wrapper: void every DC past its fee window. Safe to run twice."""
    ts = _now(now)
    today = ts.astimezone(ET).date()
    rows = _all(conn, """select c.upid from contracts c join prospect_status s using (upid)
                          where c.valid_to is null and c.kind = 'prospect' and c.prospect_type = 'DC'
                            and s.mlb_debut is not null and extract(month from s.mlb_debut) <> %s
                            and s.mlb_debut + %s < %s order by c.upid""",
                (FEE_EXEMPT_MONTH, DEBUT_FEE_WINDOW_DAYS, today))
    done = [void_dc(conn, r["upid"], created_by=created_by, season=season, now=ts)["message"] for r in rows]
    return {"voided": len(done), "message": f"{len(done)} development contract(s) voided."}


def over_fbp_limits(status, limits) -> list[str]:
    """Which FBP prospect limits a prospect_status row is over (Art. III s05)."""
    over = []
    if status.get("pa") is not None and status["pa"] > limits["pa"]:
        over.append(f"{status['pa']} PA (limit {limits['pa']})")
    if status.get("ip") is not None and status["ip"] > limits["ip"]:
        over.append(f"{status['ip']} IP (limit {limits['ip']})")
    if status.get("appearances") is not None and status["appearances"] > limits["appearances"]:
        over.append(f"{status['appearances']} appearances (limit {limits['appearances']})")
    if status.get("age") is not None and status["age"] >= limits["age"]:
        over.append(f"age {status['age']} (limit {limits['age']})")
    return over


@_atomic
def graduate(conn, upid, *, created_by, season=None, team=None, forced=False, slot="active", now=None):
    """End the prospect contract and open a keeper contract: TC-BC-1 (BC), TC-1 (international rookie), else TC-R."""
    ts, season = _ts_season(conn, now, season)
    if slot not in ("active", "na"):
        raise RuleError("a graduate goes to an active or NA slot")
    c = _locked_contract(conn, upid, team=team, kind="prospect")
    name = _player(conn, upid)["name"]
    if forced:
        lim = _one(conn, "select * from prospect_limits where season = %s", (season,))
        st = _one(conn, "select * from prospect_status where upid = %s", (upid,))
        over = over_fbp_limits(st, lim) if st and lim else []
        if not over:
            raise RuleError(f"{name} is not over any FBP prospect limit")
    if c["prospect_type"] == "BC":
        entry = "graduate_bc"
    elif c["international_rookie"]:
        entry = "graduate_international"
    else:
        entry = "graduate"
    tier = _entry_tier(conn, entry)
    if slot == "active" and not forced:
        _check_can_activate(conn, c["team_abbr"], upid, season)
    _close_contract(conn, c["id"], "graduated", ts)
    cid = _open_contract(conn, upid=upid, team_abbr=c["team_abbr"], kind="keeper", tier=tier,
                         acquired_via="graduation", international_rookie=c["international_rookie"], valid_from=ts)
    _set_slot(conn, upid, slot, ts, season)
    _roster_changed(conn, c["team_abbr"], [upid], ts)
    msg = f"{name} graduated from {c['prospect_type']} to a {tier} keeper contract ({slot} slot)."
    _event(conn, upid, "graduation", msg, team=c["team_abbr"], season=season, by=created_by, ts=ts, contract=tier)
    return {"contract_id": cid, "tier": tier, "message": msg}


@_atomic
def retain_bc(conn, upid, *, created_by="system", season=None, choice=None, snapshot_date=None, now=None):
    """At PAD: a BC in the official Nov 1 Top 100 keeps BC free.

    A BC outside the list is not changed unless `choice` is given:
    'repurchase' (pay the BC price again) or 'DC' / 'PC' (drop to that contract at its PAD price).
    """
    ts, season = _ts_season(conn, now, season)
    c = _locked_contract(conn, upid, kind="prospect")
    name = _player(conn, upid)["name"]
    if c["bc_decision_season"] == season:   # idempotent: a second call never charges again
        d = c["bc_decision"]
        return {"retained": d == "retained", "charged": 0, "already_decided": True, "prospect_type": c["prospect_type"],
                "message": f"{name}: the {season} Blue Chip decision is already recorded ({d}); nothing charged."}
    if c["prospect_type"] != "BC":
        raise RuleError(f"{name} is not on a Blue Chip contract")
    if snapshot_date is None:
        snap = _one(conn, """select max(snapshot_date) as d from top100_snapshots
                              where official and snapshot_date >= make_date(%s, 1, 1)
                                and snapshot_date < make_date(%s, 1, 1)""", (season - 1, season))
        snapshot_date = snap["d"] if snap else None
    if snapshot_date is None:
        raise RuleError(f"no official Top 100 snapshot for {season - 1}")
    on_list = _one(conn, "select rank from top100_snapshots where official and snapshot_date = %s and upid = %s",
                   (snapshot_date, upid))
    st = _one(conn, "select over_mlb_limits, over_fbp_limits from prospect_status where upid = %s", (upid,))
    # Art. III s04: over MLB rookie limits but not FBP limits and not graduated still retains
    exempt = bool(st and st["over_mlb_limits"] and not st["over_fbp_limits"])
    if on_list or exempt:
        _exec(conn, "update contracts set bc_decision = 'retained', bc_decision_season = %s where id = %s", (season, c["id"]))
        msg = f"{name} keeps his Blue Chip contract free" + (f" (Top 100 #{on_list['rank']})." if on_list else " (over MLB limits only).")
        _event(conn, upid, "bc_retained", msg, team=c["team_abbr"], season=season, by=created_by, ts=ts, contract="BC")
        return {"retained": True, "charged": 0, "message": msg}
    if choice is None:
        return {"retained": False, "needs_decision": True,
                "message": f"{name} is not on the {snapshot_date} Top 100: repurchase BC or choose DC/PC."}
    if choice == "repurchase":
        price, new_type = _price(conn, season, "bc"), "BC"
    elif choice in ("DC", "PC"):
        price, new_type = _price(conn, season, choice.lower()), choice
    else:
        raise RuleError("choice must be 'repurchase', 'DC' or 'PC'")
    _spend(conn, c["team_abbr"], price, "prospect_contract", "PAD", season, upid=upid, ref_type="bc_retain",
           ref_id=f"{season}:{upid}", note=f"BC not retained; {choice}", by=created_by)
    _exec(conn, "update contracts set prospect_type = %s, bc_decision = %s, bc_decision_season = %s where id = %s",
          (new_type, choice, season, c["id"]))
    msg = f"{name} was not in the Top 100; {c['team_abbr']} paid ${price} and he is now {new_type}."
    _event(conn, upid, "bc_not_retained", msg, team=c["team_abbr"], season=season, by=created_by, ts=ts, contract=new_type)
    return {"retained": False, "charged": price, "prospect_type": new_type, "message": msg}


# ----------------------------------------------------------------- keepers

def _already_kept(conn, upid, season):
    return _one(conn, """select 1 as x from wb_ledger where upid = %s and kind = 'keeper_salary'
                          and ref_type = 'kap' and ref_id = %s""", (upid, str(season))) is not None


def _kap_keep(conn, c, *, season, by, ts, tier_paid, il=False, rat=False):
    """Charge `tier_paid`'s keeper cost (less IL discount), advance the contract from `tier_paid`."""
    t = _tier(conn, tier_paid)
    if t["keeper_cost"] is None:
        raise RuleError(f"tier {tier_paid} has no keeper cost")
    cost = t["keeper_cost"]
    if il:
        if t["il_discount"] is None:
            raise RuleError(f"{tier_paid} contracts are not eligible for an IL tag")
        cost = max(cost - t["il_discount"], 0)
    name = _player(conn, c["upid"])["name"]
    note = f"{name} kept at {tier_paid}" + (" with IL tag" if il else "") + (" after Reduce a Tier" if rat else "")
    if cost:
        _spend(conn, c["team_abbr"], cost, "keeper_salary", "KAP", season, upid=c["upid"], ref_type="kap",
               ref_id=str(season), note=note, by=by)
    else:  # zero-cost keep still leaves the marker row that makes a second keep impossible
        _credit(conn, c["team_abbr"], 0, "keeper_salary", "KAP", season, upid=c["upid"], ref_type="kap",
                ref_id=str(season), note=note, by=by)
    nxt = t["next_tier"]
    _exec(conn, "update contracts set tier = %s where id = %s", (nxt, c["id"]))
    return cost, nxt, note


@_atomic
def keep_player(conn, upid, *, created_by, season=None, team=None, now=None):
    """KAP: charge the stored tier's keeper cost, then advance to next_tier for the following season."""
    ts, season = _ts_season(conn, now, season)
    _require_window(conn, season, "KAP", ts, "keeping a player")
    c = _locked_contract(conn, upid, team=team, kind="keeper")
    if _already_kept(conn, upid, season):
        raise RuleError(f"{_player(conn, upid)['name']} was already kept in the {season} KAP")
    cost, nxt, note = _kap_keep(conn, c, season=season, by=created_by, ts=ts, tier_paid=c["tier"])
    msg = f"{note}: ${cost} charged; now {nxt}."
    _event(conn, upid, "keeper_kept", msg, team=c["team_abbr"], season=season, by=created_by, ts=ts, contract=nxt)
    return {"charged": cost, "tier": nxt, "message": msg}


def _il_ineligible_until(conn, season, ts):
    """KAP end date + 5 weeks, from seasons.dates['kap_end_date']; if the season has no such date, today + 35 days."""
    row = _one(conn, "select dates->>'kap_end_date' as d from seasons where year = %s", (season,))
    if row and row["d"]:
        return date.fromisoformat(row["d"]) + timedelta(days=IL_INELIGIBLE_DAYS)
    return ts.astimezone(ET).date() + timedelta(days=IL_INELIGIBLE_DAYS)


@_atomic
def il_tag(conn, upid, *, created_by, approved_by, season=None, team=None, now=None):
    """KAP: keep with the IL discount (TC -10, VC -15, FC -35). One per contract family per team. Admin approves."""
    ts, season = _ts_season(conn, now, season)
    if not approved_by:
        raise RuleError("an admin must approve an IL tag")
    _require_window(conn, season, "KAP", ts, "an IL tag")
    c = _locked_contract(conn, upid, team=team, kind="keeper")
    name = _player(conn, upid)["name"]
    t = _tier(conn, c["tier"])
    if t["il_discount"] is None:
        raise RuleError(f"{name} is on {c['tier']}, which can't take an IL tag")
    if _already_kept(conn, upid, season):
        raise RuleError(f"{name} was already kept in the {season} KAP")
    # open AND closed contracts count: a tagged player who was released or traded still used the allowance
    used = _one(conn, """select 1 as x from contracts where il_tag_team = %s and il_tag
                          and il_tag_season = %s and il_tag_family = %s""", (c["team_abbr"], season, t["family"]))
    if used:
        raise RuleError(f"{c['team_abbr']} already used its {t['family']} IL tag for {season}")
    cost, nxt, note = _kap_keep(conn, c, season=season, by=created_by, ts=ts, tier_paid=c["tier"], il=True)
    _exec(conn, """update contracts set il_tag = true, il_tag_season = %s, il_tag_family = %s, il_tag_team = %s,
                          il_ineligible_until = %s where id = %s""",
          (season, t["family"], c["team_abbr"], _il_ineligible_until(conn, season, ts), c["id"]))
    msg = f"{note}: ${cost} charged (approved by {approved_by}); now {nxt}."
    _event(conn, upid, "il_tag", msg, team=c["team_abbr"], season=season, by=created_by, ts=ts, contract=nxt)
    return {"charged": cost, "tier": nxt, "message": msg}


@_atomic
def reduce_a_tier(conn, upid, *, created_by, season=None, team=None, now=None):
    """KAP: pay the RaT price, drop the stored tier one step, pay that tier's cost, advance from it."""
    ts, season = _ts_season(conn, now, season)
    _require_window(conn, season, "KAP", ts, "Reduce a Tier")
    c = _locked_contract(conn, upid, team=team, kind="keeper")
    name = _player(conn, upid)["name"]
    t = _tier(conn, c["tier"])
    if t["family"] == "TC" or t["lower_tier"] is None:
        raise RuleError(f"{name} is on {c['tier']}, which can't be reduced (not on TC, and not into TC)")
    if _already_kept(conn, upid, season):
        raise RuleError(f"{name} was already kept in the {season} KAP")
    rat_price = _price(conn, season, "rat")
    _spend(conn, c["team_abbr"], rat_price, "rat", "KAP", season, upid=upid, ref_type="kap", ref_id=str(season),
           note=f"Reduce a Tier for {name}: {c['tier']} to {t['lower_tier']}", by=created_by)
    cost, nxt, note = _kap_keep(conn, c, season=season, by=created_by, ts=ts, tier_paid=t["lower_tier"], rat=True)
    _exec(conn, "update contracts set rat_count = rat_count + 1 where id = %s", (c["id"],))
    msg = f"{name} reduced {c['tier']} to {t['lower_tier']}: ${rat_price} RaT + ${cost} salary; now {nxt}."
    _event(conn, upid, "reduce_a_tier", msg, team=c["team_abbr"], season=season, by=created_by, ts=ts, contract=nxt)
    return {"charged": rat_price + cost, "rat_price": rat_price, "salary": cost, "tier": nxt, "message": msg}


@_atomic
def assign_keeper_draft_pick(conn, upid, team, draft_round, *, created_by, season=None, now=None):
    """Keeper Draft pick: tier comes from the round (rounds 1-3 VC-1, others TC-1), not from carried_tier."""
    ts, season = _ts_season(conn, now, season)
    _team(conn, team)
    p = _player(conn, upid)
    _lock(conn, team)
    if _one(conn, "select 1 as x from contracts where upid = %s and valid_to is null", (upid,)):
        raise RuleError(f"{p['name']} already has an open contract")
    entry = f"keeper_draft_r{draft_round}"
    if not _one(conn, "select 1 as x from contract_entry_tiers where entry = %s", (entry,)):
        entry = "keeper_draft_other"
    tier = _entry_tier(conn, entry)
    cid = _open_contract(conn, upid=upid, team_abbr=team, kind="keeper", tier=tier, acquired_via="keeper_draft",
                         acquired_round=draft_round, valid_from=ts)
    _set_slot(conn, upid, "active", ts)
    msg = f"{team} drafted {p['name']} in round {draft_round}: {tier} contract."
    _event(conn, upid, "keeper_draft", msg, team=team, season=season, by=created_by, ts=ts, contract=tier)
    return {"contract_id": cid, "tier": tier, "message": msg}


# ------------------------------------------------------- release / FA / slots

@_atomic
def release(conn, upid, *, created_by, season=None, team=None, reason="released", now=None):
    """End the team's contract. The player keeps his tier as carried_tier for the next owner."""
    ts, season = _ts_season(conn, now, season)
    if reason not in ("released", "not_kept"):
        raise RuleError("reason must be 'released' or 'not_kept'")
    c = _locked_contract(conn, upid, team=team)
    name = _player(conn, upid)["name"]
    _close_contract(conn, c["id"], reason, ts)
    if c["kind"] == "keeper":
        _exec(conn, "update players set carried_tier = %s where upid = %s", (c["tier"], upid))
    _exec(conn, "delete from roster_slots where upid = %s", (upid,))
    _roster_changed(conn, c["team_abbr"], [upid], ts)
    carried = f" He keeps his {c['tier']} tier." if c["kind"] == "keeper" else ""
    msg = f"{c['team_abbr']} {reason.replace('_', ' ')} {name}.{carried}"
    _event(conn, upid, reason, msg, team=c["team_abbr"], season=season, by=created_by, ts=ts, contract=c["tier"] or c["prospect_type"])
    return {"carried_tier": c["tier"] if c["kind"] == "keeper" else None, "message": msg}


@_atomic
def acquire_free_agent(conn, upid, team, *, created_by, season=None, slot="active", now=None):
    """Open a keeper contract at the player's carried_tier (TC-1 if he has none)."""
    ts, season = _ts_season(conn, now, season)
    _team(conn, team)
    p = _player(conn, upid)
    _lock(conn, team)
    if slot not in SLOTS:
        raise RuleError(f"slot must be one of {SLOTS}")
    if _one(conn, "select 1 as x from contracts where upid = %s and valid_to is null", (upid,)):
        raise RuleError(f"{p['name']} is owned and can't be added as a free agent")
    tier = p["carried_tier"] or _entry_tier(conn, "free_agent_default")
    if slot == "active":
        _check_can_activate(conn, team, upid, season)
    cid = _open_contract(conn, upid=upid, team_abbr=team, kind="keeper", tier=tier, acquired_via="free_agent", valid_from=ts)
    _set_slot(conn, upid, slot, ts, season)
    msg = f"{team} added {p['name']} as a free agent at {tier}."
    _event(conn, upid, "free_agent_add", msg, team=team, season=season, by=created_by, ts=ts, contract=tier)
    return {"contract_id": cid, "tier": tier, "message": msg}


@_atomic
def move_slot(conn, upid, slot, *, created_by, season=None, team=None, now=None):
    """Move a player between active, il, na and farm, checking the roster maximums for active."""
    ts, season = _ts_season(conn, now, season)
    if slot not in SLOTS:
        raise RuleError(f"slot must be one of {SLOTS}")
    c = _locked_contract(conn, upid, team=team)
    cur = _one(conn, "select slot from roster_slots where upid = %s", (upid,))
    if cur and cur["slot"] == slot:
        return {"slot": slot, "message": "already there"}
    if slot == "active":
        _check_can_activate(conn, c["team_abbr"], upid, season)
    _set_slot(conn, upid, slot, ts, season)
    if cur and cur["slot"] == "active":
        _roster_changed(conn, c["team_abbr"], [upid], ts)
    name = _player(conn, upid)["name"]
    msg = f"{name} moved to the {slot} slot."
    _event(conn, upid, "slot_move", msg, team=c["team_abbr"], season=season, by=created_by, ts=ts)
    return {"slot": slot, "message": msg}


@_atomic
def transfer(conn, upid, from_team, to_team, *, created_by, season=None, ref_id=None, now=None):
    """Trade: close the contract ('traded') and open an identical one for the new team."""
    ts, season = _ts_season(conn, now, season)
    _team(conn, from_team)
    _team(conn, to_team)
    if from_team == to_team:
        raise RuleError("a trade needs two different teams")
    _lock(conn, from_team, to_team)
    c = _one(conn, "select * from contracts where upid = %s and valid_to is null for update", (upid,))
    name = _player(conn, upid)["name"]
    if not c or c["team_abbr"] != from_team:
        raise RuleError(f"{name} is not on {from_team}")
    _close_contract(conn, c["id"], "traded", ts)
    cid = _open_contract(
        conn, upid=upid, team_abbr=to_team, kind=c["kind"], tier=c["tier"], prospect_type=c["prospect_type"],
        acquired_via="trade", acquired_round=c["acquired_round"], il_tag=c["il_tag"], il_tag_season=c["il_tag_season"],
        il_tag_family=c["il_tag_family"], il_tag_team=c["il_tag_team"], il_ineligible_until=c["il_ineligible_until"],
        bc_decision=c["bc_decision"], bc_decision_season=c["bc_decision_season"], rat_count=c["rat_count"], international_rookie=c["international_rookie"],
        valid_from=ts)
    _roster_changed(conn, from_team, [upid], ts)
    msg = f"{name} traded from {from_team} to {to_team}" + (f" ({ref_id})." if ref_id else ".")
    _event(conn, upid, "trade", msg, team=to_team, season=season, by=created_by, ts=ts, contract=c["tier"] or c["prospect_type"])
    return {"contract_id": cid, "message": msg}


# ------------------------------------------------------------------- money

@_atomic
def wb_spend(conn, team, amount, kind, phase, season, *, created_by, upid=None, ref_type=None, ref_id=None, note=None):
    """Low-level spend (amount > 0). Fails with 'insufficient WizBucks' and no ledger row if short.

    Only SPEND_KINDS. A penalty goes through penalty(); admin_adjustment and reversal through admin_adjust().
    """
    if kind not in SPEND_KINDS:
        raise RuleError(f"wb_spend can't write a {kind!r} row; allowed: {', '.join(SPEND_KINDS)}")
    _team(conn, team)
    _lock(conn, team)
    lid = _spend(conn, team, amount, kind, phase, season, upid=upid, ref_type=ref_type, ref_id=ref_id, note=note, by=created_by)
    bal = balance(conn, team)
    return {"ledger_id": lid, "balance": bal, "message": f"{team} spent ${amount} ({kind}); balance ${bal}."}


@_atomic
def wb_credit(conn, team, amount, kind, phase, season, *, created_by, upid=None, ref_type=None, ref_id=None, note=None):
    """Low-level credit (amount > 0; an opening_balance may be 0). Only CREDIT_KINDS."""
    if kind not in CREDIT_KINDS:
        raise RuleError(f"wb_credit can't write a {kind!r} row; allowed: {', '.join(CREDIT_KINDS)}")
    if amount is None or amount < 0 or (amount == 0 and kind != "opening_balance"):
        raise RuleError("a credit must be a positive amount")
    _team(conn, team)
    _lock(conn, team)
    lid = _credit(conn, team, amount, kind, phase, season, upid=upid, ref_type=ref_type, ref_id=ref_id, note=note, by=created_by)
    bal = balance(conn, team)
    return {"ledger_id": lid, "balance": bal, "message": f"{team} received ${amount} ({kind}); balance ${bal}."}


@_atomic
def penalty(conn, team, amount, *, rule, created_by, season=None, phase="IN_SEASON", dates=None, upid=None, now=None):
    """Admin confirms a detected violation. The only spend allowed to take a balance below zero."""
    ts, season = _ts_season(conn, now, season)
    if not rule:
        raise RuleError("a penalty needs the rule that was broken")
    if created_by == "system":
        raise RuleError("a penalty is confirmed by an admin, not the system")
    _team(conn, team)
    _lock(conn, team)
    note = f"{rule}" + (f" ({dates})" if dates else "")
    lid = _spend(conn, team, amount, "penalty", phase, season, upid=upid, ref_type="penalty", ref_id=rule, note=note, by=created_by)
    msg = f"{team} fined ${amount}: {note}."
    _event(conn, upid, "penalty", msg, team=team, season=season, by=created_by, ts=ts)
    return {"ledger_id": lid, "balance": balance(conn, team), "message": msg}


@_atomic
def admin_adjust(conn, *, reason, created_by, season=None, phase="IN_SEASON", team=None, amount=None, upid=None,
                 kind="admin_adjustment", reverses_ledger_id=None, set_tier=None, set_prospect_type=None, now=None):
    """Ledger and/or contract fix with a required reason. A negative amount may not overdraw the balance.

    The only path for 'admin_adjustment' and 'reversal' rows. A reversal must name the ledger row it
    reverses (`reverses_ledger_id`); it is recorded with ref_type='reversal', ref_id=<that id>, and a row
    can be reversed once.
    """
    ts, season = _ts_season(conn, now, season)
    if not (reason and reason.strip()):
        raise RuleError("an admin adjustment needs a reason")
    if kind not in ("admin_adjustment", "reversal"):
        raise RuleError("kind must be 'admin_adjustment' or 'reversal'")
    out = {}
    notes = []
    ref_type, ref_id = "admin", None
    if kind == "reversal":
        if reverses_ledger_id is None:
            raise RuleError("a reversal must name the ledger row it reverses")
        ref_type, ref_id = "reversal", str(reverses_ledger_id)
        orig = _one(conn, "select team_abbr from wb_ledger where id = %s", (reverses_ledger_id,))
        if not orig:
            raise RuleError(f"ledger row {reverses_ledger_id} does not exist")
        if team is not None and team != orig["team_abbr"]:
            raise RuleError(f"ledger row {reverses_ledger_id} belongs to {orig['team_abbr']}, not {team}")
        team = orig["team_abbr"]
        if _one(conn, "select 1 as x from wb_ledger where ref_type = 'reversal' and ref_id = %s", (ref_id,)):
            raise RuleError(f"ledger row {reverses_ledger_id} was already reversed")
    elif reverses_ledger_id is not None:
        raise RuleError("reverses_ledger_id is only for kind='reversal'")
    if amount:
        if not team:
            raise RuleError("a ledger adjustment needs a team")
        _team(conn, team)
        _lock(conn, team)
        if amount > 0:
            out["ledger_id"] = _credit(conn, team, amount, kind, phase, season, upid=upid, ref_type=ref_type, ref_id=ref_id, note=reason, by=created_by)
        else:
            out["ledger_id"] = _spend(conn, team, -amount, kind, phase, season, upid=upid, ref_type=ref_type, ref_id=ref_id, note=reason, by=created_by)
        notes.append(f"{team} {amount:+d} WB")
    if set_tier or set_prospect_type:
        if not upid:
            raise RuleError("a contract fix needs a upid")
        c = _locked_contract(conn, upid)
        if set_tier:
            _tier(conn, set_tier)
            if c["kind"] != "keeper" or set_tier == "P":
                raise RuleError("a keeper tier can only be set on a keeper contract, and not to 'P'")
            _exec(conn, "update contracts set tier = %s where id = %s", (set_tier, c["id"]))
            notes.append(f"tier {c['tier']} to {set_tier}")
        if set_prospect_type:
            if c["kind"] != "prospect" or set_prospect_type not in PROSPECT_TYPES:
                raise RuleError("a prospect type can only be set on a prospect contract")
            _exec(conn, "update contracts set prospect_type = %s where id = %s", (set_prospect_type, c["id"]))
            notes.append(f"prospect type {c['prospect_type']} to {set_prospect_type}")
        team = team or c["team_abbr"]
    if kind == "reversal" and "ledger_id" not in out:
        raise RuleError("a reversal needs a non-zero amount")
    if not notes:
        raise RuleError("nothing to adjust")
    msg = f"Admin fix by {created_by}: " + "; ".join(notes) + f". Reason: {reason}"
    _event(conn, upid, "admin_adjust", msg, team=team, season=season, by=created_by, ts=ts)
    out["message"] = msg
    return out


# ------------------------------------------------------------ automatic jobs

_LEDGER_PHASE = {"PRIOR_SEASON": "OFFSEASON"}


@_atomic
def close_phase_rollover(conn, season, from_phase, to_phase, created_by="system"):
    """Carry up to the cap into the next phase and forfeit the rest, both as ledger rows. Safe to run twice.

    Writes, per team: a `rollover_forfeit` row for the excess (if any) and a `rollover_in` row
    recording what was carried. The carried money is not moved (the balance is the ledger), so the
    rollover_in row has amount 0 and the carried amount is in its note; it also marks the team done.
    Refuses (RuleError, nothing written) if a team that hasn't been rolled over yet already has the next
    phase's installment: the installment would be counted as carried money.
    """
    cap_row = _one(conn, "select cap from wb_rollover_caps where season = %s and from_phase = %s and to_phase = %s",
                   (season, from_phase, to_phase))
    if not cap_row:
        raise RuleError(f"no rollover cap for {from_phase} to {to_phase} in {season}")
    cap = cap_row["cap"]
    ref_id = f"{season}:{from_phase}>{to_phase}"
    out_phase = _LEDGER_PHASE.get(from_phase, from_phase)
    results = []
    for t in _all(conn, "select abbr from teams where active order by abbr"):
        abbr = t["abbr"]
        _lock(conn, abbr)
        if _one(conn, "select 1 as x from wb_ledger where team_abbr = %s and kind = 'rollover_in' and ref_type = 'rollover' and ref_id = %s",
                (abbr, ref_id)):
            continue
        nxt = f"{season}:{to_phase}"
        if _one(conn, "select 1 as x from wb_ledger where team_abbr = %s and kind = 'installment' and ref_type = 'installment' and ref_id = %s",
                (abbr, nxt)):
            raise RuleError(f"{abbr} already has its {to_phase} installment for {season}: the rollover must run before it "
                            "or the installment would be counted as carried money")
        bal = balance(conn, abbr)
        carried = max(min(bal, cap), 0)
        forfeit = max(bal - carried, 0)
        if forfeit:
            _spend(conn, abbr, forfeit, "rollover_forfeit", out_phase, season, ref_type="rollover", ref_id=ref_id,
                   note=f"{from_phase} to {to_phase}: ${bal} on hand, cap ${cap}, forfeited ${forfeit}", by=created_by)
        _credit(conn, abbr, 0, "rollover_in", to_phase if to_phase in ("PAD", "KAP", "APA") else "OFFSEASON", season,
                ref_type="rollover", ref_id=ref_id, note=f"{from_phase} to {to_phase}: carried ${carried} of ${bal} (cap ${cap})",
                by=created_by)
        results.append({"team": abbr, "carried": carried, "forfeited": forfeit})
    return {"teams": results, "message": f"{from_phase} to {to_phase} rollover: {len(results)} team(s) processed."}


@_atomic
def credit_installment(conn, season, installment, created_by="system"):
    """Credit PAD / KAP / APA / PTDA by bracket, plus finish rewards. Safe to run twice."""
    if installment not in ("PAD", "KAP", "APA", "PTDA"):
        raise RuleError("installment must be PAD, KAP, APA or PTDA")
    phase = "IN_SEASON" if installment == "PTDA" else installment
    ref_id = f"{season}:{installment}"
    results, missing = [], []
    teams = _all(conn, "select abbr from teams where active order by abbr")
    for t in teams:
        abbr = t["abbr"]
        _lock(conn, abbr)
        brackets = {"PAD": season - 1, "PTDA": season}.get(installment)
        if brackets is None:
            bracket, rank = "all", None
        else:
            ts_row = _one(conn, "select bracket, final_rank from team_seasons where team_abbr = %s and season = %s", (abbr, brackets))
            if not ts_row or not ts_row["bracket"]:
                missing.append(abbr)
                continue
            bracket, rank = ts_row["bracket"], ts_row["final_rank"]
        amt = _one(conn, "select amount from wb_installments where season = %s and installment = %s and bracket = %s",
                   (season, installment, bracket))
        if not amt:
            raise RuleError(f"no {installment} installment for bracket {bracket!r} in {season}")
        if installment == "KAP":   # finish rewards use the prior season's final rank
            ts_row = _one(conn, "select final_rank from team_seasons where team_abbr = %s and season = %s", (abbr, season - 1))
            rank = ts_row["final_rank"] if ts_row else None
        row = {"team": abbr, "amount": 0, "reward": 0}
        if not _one(conn, "select 1 as x from wb_ledger where team_abbr = %s and kind = 'installment' and ref_type = 'installment' and ref_id = %s",
                    (abbr, ref_id)):
            _credit(conn, abbr, amt["amount"], "installment", phase, season, ref_type="installment", ref_id=ref_id,
                    note=f"{installment} installment ({bracket})", by=created_by)
            row["amount"] = amt["amount"]
        if rank is not None and not _one(conn, "select 1 as x from wb_ledger where team_abbr = %s and kind = 'finish_reward' and ref_type = 'installment' and ref_id = %s",
                                         (abbr, ref_id)):
            rw = _one(conn, "select amount from wb_finish_rewards where season = %s and installment = %s and final_rank = %s",
                      (season, installment, rank))
            if rw:
                _credit(conn, abbr, rw["amount"], "finish_reward", phase, season, ref_type="installment", ref_id=ref_id,
                        note=f"{installment} finish reward for finishing {rank} in {season - 1}", by=created_by)
                row["reward"] = rw["amount"]
        results.append(row)
    if missing:
        raise RuleError(f"{installment} {season} needs a bracket on team_seasons for: {', '.join(missing)}")
    return {"teams": results, "message": f"{installment} {season}: credited {len(results)} team(s)."}


# ------------------------------------------------------- reversing a draft pick (draft room corrections)

@_atomic
def reverse_draft_pick(conn, upid, team, *, created_by, expected_tier=None, expected_prospect_type=None, season=None,
                       note=None, now=None):
    """Undo a draft pick's contract (undo / swap / void in the draft room). Only a contract that is still exactly what
    the draft gave: open, on `team`, acquired via prospect_draft or keeper_draft, no IL tag, no RAT, no BC decision,
    and still the tier (keeper) or prospect type (prospect) the draft gave, passed in as expected_tier /
    expected_prospect_type. Otherwise RuleError says why (an upgraded DC, a traded player...).
    Closes the contract (end_reason 'voided'), deletes the roster slot, frees the prepaid prospect slot and writes a
    player_events row."""
    ts, season = _ts_season(conn, now, season)
    if expected_tier is None and expected_prospect_type is None:
        raise RuleError("reverse_draft_pick needs the tier or prospect type the draft gave")
    name = _player(conn, upid)["name"]
    c = _locked_contract(conn, upid, team=team)      # takes the team lock; RuleError if no open contract or another team
    if c["acquired_via"] not in ("prospect_draft", "keeper_draft"):
        raise RuleError(f"{name}'s contract came from {c['acquired_via']}, not a draft pick")
    if c["il_tag"]:
        raise RuleError(f"{name} has been IL-tagged since the draft")
    if c["rat_count"]:
        raise RuleError(f"{name} has a RAT on his contract since the draft")
    if c["bc_decision"] is not None:
        raise RuleError(f"{name} has a BC decision recorded ({c['bc_decision']})")
    if expected_prospect_type is not None and c["prospect_type"] != expected_prospect_type:
        raise RuleError(f"{name}'s contract is now {c['prospect_type'] or c['tier']}, not the {expected_prospect_type} the pick gave "
                        "(upgraded or changed since the draft)")
    if expected_tier is not None and c["tier"] != expected_tier:
        raise RuleError(f"{name}'s contract is now {c['tier'] or c['prospect_type']}, not the {expected_tier} the pick gave "
                        "(changed since the draft)")
    _close_contract(conn, c["id"], "voided", ts)
    _exec(conn, "delete from roster_slots where upid = %s", (upid,))
    _exec(conn, "update prospect_draft_slots set filled_upid = null, filled_at = null where filled_upid = %s", (upid,))
    msg = f"{team}'s draft pick ({note or 'draft'}) was reversed; {name} is back in the pool."
    _event(conn, upid, "draft_pick_reversed", msg, team=team, season=season, by=created_by, ts=ts,
           contract=c["tier"] or c["prospect_type"])
    return {"contract_id": c["id"], "message": msg}
