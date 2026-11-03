"""Draft room database layer (specs/04_draft_room.md).

Every function takes a psycopg connection and opens its own `with conn.transaction():`. Anything
that changes a draft takes pg_advisory_xact_lock(hashtext(draft_id)) first, so picks, clock ticks
and admin corrections take turns. Use a connection with autocommit on (or one that has not run a
query yet) so that transaction is the outermost one; inside a caller's transaction these become
savepoints and the caller commits.

Eligibility is decided in one place: the SQL function draft_pool(draft_id, for_round)
(db/migrations/0004_draft.sql). Pick checks, autopick, search and the pool check all call it.

make_pick / make_picks are the only way a pick happens. Contracts are opened through
league.actions (assign_prospect_contract / assign_keeper_draft_pick), which also write the
player_events row. Notifications are rows in the `notifications` outbox; nothing is sent from here.

The picks table name lives in ONE constant, PICKS_TABLE. SQL in this module writes it as @picks and the
helpers below fill it in, so the cutover rename (scripts/db/cutover_draft_rename.sql) is a one-line change here.

Errors: DraftError (message safe to show a manager; .status is the HTTP status the API should use,
403 for "not your turn").

`now` is passed in everywhere so tests and the simulator control time; None means the real clock.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from league import actions
from league.actions import RuleError

log = logging.getLogger(__name__)

# The picks table (the spec's "draft_picks"). It is draft_picks_live until the cutover renames the Spec 01
# mirror away; then change this ONE line to "draft_picks" (see scripts/db/cutover_draft_rename.sql).
PICKS_TABLE = "draft_picks_live"
# Clock lengths per draft type live in the draft_defaults table (db/migrations/0004_draft.sql), nowhere else.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
WARNING_SECONDS = 60            # the 60-second warning
HEARTBEAT_STALE_SECONDS = 15    # runner heartbeat older than this = the runner was down
RESTART_MIN_SECONDS = 60        # after an outage the clock is at least this long
TICKER_SIZE = 8
SEARCH_LIMIT = 25
BOARD_MAX = 50
SOURCES = ("web", "autopick", "forklift", "admin")
SYSTEM = "system"


class DraftError(Exception):
    """A draft rule stopped the action. The message is safe to show a manager."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class AwardsPaid(DraftError, RuleError):
    """A correction was refused because the season's speed awards are already paid."""


# ------------------------------------------------------------------ plumbing

def _sql(sql):
    return sql.replace("@picks", PICKS_TABLE)


def _one(conn, sql, params=()):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_sql(sql), params)
        return cur.fetchone()


def _all(conn, sql, params=()):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_sql(sql), params)
        return cur.fetchall()


def _exec(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(_sql(sql), params)
        return cur.rowcount


def _now(now):
    return now if now is not None else datetime.now(timezone.utc)


def _lock(conn, draft_id):
    _exec(conn, "select pg_advisory_xact_lock(hashtext(%s))", (draft_id,))


def _draft(conn, draft_id):
    d = _one(conn, "select * from drafts where draft_id = %s", (draft_id,))
    if not d:
        raise DraftError(f"Unknown draft {draft_id!r}", 404)
    return d


def _clock(conn, draft_id):
    return _one(conn, "select * from draft_clock where draft_id = %s", (draft_id,))


def _slot(conn, draft_id, overall):
    return _one(conn, "select * from draft_slots where draft_id = %s and overall = %s", (draft_id, overall))


def _label(slot):
    return f"{slot['round']}.{slot['pick_in_round']:02d}"


def _event(conn, draft_id, kind, actor, detail, now):
    _exec(conn, "insert into draft_events (draft_id, kind, actor, detail, at) values (%s, %s, %s, %s, %s)",
          (draft_id, kind, actor, Jsonb(detail) if detail is not None else None, now))


def _notify(conn, kind, draft_id, payload, now, team=None):
    """Queue an outbox row. team = a DM to that manager; None = the draft channel (or admins, see payload.audience)."""
    _exec(conn, "insert into notifications (kind, team_abbr, payload, created_at) values (%s, %s, %s, %s)",
          (kind, team, Jsonb({"draft_id": draft_id, **payload}), now))


def _alert(conn, draft_id, message, now):
    _notify(conn, "admin_alert", draft_id, {"audience": "admins", "message": message}, now)


def _is_forklift(conn, draft_id, team):
    return _one(conn, "select 1 as x from draft_forklift where draft_id = %s and team_abbr = %s", (draft_id, team)) is not None


def _tag(n):
    return f"{n} Time" if n > 1 else None


def _player_name(conn, upid):
    r = _one(conn, "select name from players where upid = %s", (upid,))
    return r["name"] if r else upid


def _open_slots(conn, draft_id, exclude=None, limit=None):
    """Draftable slots with no pick, in the order they will be taken: makeup picks first (in the order
    they were voided), then by overall. `exclude` leaves out the slot that is on the clock."""
    sql = """select s.* from draft_slots s
               left join @picks p on p.draft_id = s.draft_id and p.overall = s.overall
              where s.draft_id = %s and s.skip_reason is null and p.overall is null
                and s.overall is distinct from %s
              order by (s.makeup_seq is null), s.makeup_seq, s.overall"""
    if limit:
        sql += f" limit {int(limit)}"
    return _all(conn, sql, (draft_id, exclude))


def _run(on_clock, upcoming):
    """The run of back-to-back picks (2 Time, 3 Time) that starts at the slot on the clock."""
    run = [on_clock]
    for s in upcoming:
        if s["team_abbr"] != on_clock["team_abbr"]:
            break
        run.append(s)
    return run


# ------------------------------------------------------------------ creating a draft

def slots_from_mirror(conn, draft, season):
    """Build the `slots` list for create_draft from Spec 01's draft_picks mirror (the 2026 order file).

    draft is 'prospect' or 'keeper'; season is the mirror's season (2026). Slots the keeper draft skips
    get their skip_reason: 'taxed' (taxed_out), 'keeper' (the "keeper" sentinel) or 'buyin_unpaid'
    (buy-in required and not purchased). round_type is mapped to the spec's names: prospect
    fypd -> 'fypd', dc -> 'open'; keeper standard -> 'vc' or 'tc' by the contract tier the round gives.
    """
    rows = _all(conn, "select round, pick, team_abbr, round_type, result, raw from draft_picks "
                      "where draft = %s and season = %s order by round, pick", (draft, season))
    if not rows:
        raise DraftError(f"The mirror has no {draft} order for {season}.")
    tiers = {r["entry"]: r["family"] for r in _all(conn, "select e.entry, t.family from contract_entry_tiers e "
                                                         "join contract_tiers t on t.tier = e.tier")}
    teams = {r["abbr"] for r in _all(conn, "select abbr from teams")}
    slots = []
    for i, r in enumerate(rows, start=1):
        raw = r["raw"] or {}
        if draft == "prospect":
            round_type = {"fypd": "fypd", "dc": "open"}.get(r["round_type"])
        else:
            family = tiers.get(f"keeper_draft_r{r['round']}", tiers.get("keeper_draft_other"))
            round_type = {"VC": "vc", "TC": "tc"}.get(family) if r["round_type"] == "standard" else None
        if round_type is None:
            raise DraftError(f"Don't know how to map {draft} round_type {r['round_type']!r} (round {r['round']}, pick {r['pick']}).")
        original = raw.get("original_owner") or r["team_abbr"]
        if original not in teams:
            alias = _one(conn, "select team_abbr from team_name_aliases where alias = %s", (original,))
            original = alias["team_abbr"] if alias else r["team_abbr"]
        if raw.get("taxed_out"):
            skip = "taxed"
        elif r["result"] == "keeper":
            skip = "keeper"
        elif raw.get("buyin_required") and not raw.get("buyin_purchased") and r["result"] is None:
            skip = "buyin_unpaid"
        else:
            skip = None
        slots.append({"overall": i, "round": r["round"], "pick_in_round": r["pick"], "original_team": original,
                      "team_abbr": r["team_abbr"], "round_type": round_type, "skip_reason": skip})
    return slots


def _db_host(conn):
    return conn.info.host or ""


def check_test_branch(conn):
    """Test drafts only run on a test database: the league_settings row `branch` must be 'test', or the database
    must be on this machine (127.0.0.1 / localhost / a unix socket). Raises DraftError otherwise."""
    host = _db_host(conn)
    if host in LOCAL_HOSTS or host.startswith("/"):
        return
    row = None
    if _one(conn, "select to_regclass('league_settings') as t")["t"]:
        row = _one(conn, "select value from league_settings where key = 'branch'")
    if row is not None and row["value"] == "test":
        return
    raise DraftError("Test drafts only run on the test database branch (league_settings branch = 'test') or a local database. "
                     f"This database is {host!r} and its branch is {row['value'] if row else 'not set'!s}.", 403)


def create_draft(conn, draft_id, season, draft_type, slots, clock_seconds=None, is_test=False,
                 forklift_seconds=None, starts_at=None):
    """Create a draft in status 'setup' with its slots. `slots` is a list of dicts with round, pick_in_round,
    original_team, team_abbr, round_type and optionally overall and skip_reason (see slots_from_mirror).
    clock_seconds / forklift_seconds left as None come from the draft_defaults table (a trigger fills them in).
    An is_test draft is refused unless the database is a test branch or local (check_test_branch)."""
    if not slots:
        raise DraftError("A draft needs slots.")
    if draft_type not in ("prospect", "keeper"):
        raise DraftError("draft_type must be 'prospect' or 'keeper'.")
    if is_test:
        check_test_branch(conn)
    with conn.transaction():
        _lock(conn, draft_id)
        try:
            with conn.transaction():
                _exec(conn, """insert into drafts (draft_id, season, draft_type, clock_seconds, forklift_seconds, starts_at, is_test)
                               values (%s, %s, %s, %s, %s, %s, %s)""",
                      (draft_id, season, draft_type, clock_seconds, forklift_seconds, starts_at, is_test))
                for i, s in enumerate(slots, start=1):
                    _exec(conn, """insert into draft_slots (draft_id, overall, round, pick_in_round, original_team, team_abbr,
                                                            round_type, skip_reason)
                                   values (%s, %s, %s, %s, %s, %s, %s, %s)""",
                          (draft_id, s.get("overall", i), s["round"], s["pick_in_round"], s["original_team"], s["team_abbr"],
                           s["round_type"], s.get("skip_reason")))
        except psycopg.errors.UniqueViolation as e:
            raise DraftError(f"Can't create {draft_id}: {e.diag.message_detail or e}") from e
        _event(conn, draft_id, "create", SYSTEM, {"slots": len(slots), "is_test": is_test}, _now(None))
    return _draft(conn, draft_id)


# ------------------------------------------------------------------ pool check and start

def open_pool_check(conn, draft_id, actor, now=None):
    now = _now(now)
    with conn.transaction():
        _lock(conn, draft_id)
        d = _draft(conn, draft_id)
        if d["status"] != "setup":
            raise DraftError(f"Pool check opens from setup; this draft is {d['status']}.", 409)
        _exec(conn, "update drafts set status = 'pool_check' where draft_id = %s", (draft_id,))
        _event(conn, draft_id, "pool_check", actor, None, now)


def preflight(conn, draft_id):
    """Problems that must be fixed before the draft opens (empty list = ready).

    * every team's draftable slots match its open prepaid prospect slots (slots are picks), prospect draft only
    * if a ranking was uploaded: every ranked player is eligible, and every eligible player is ranked
    """
    d = _draft(conn, draft_id)
    problems = []
    if d["draft_type"] == "prospect":
        bc_max = _one(conn, "select bc_slot_max_round from prospect_limits where season = %s", (d["season"],))
        if not bc_max:
            problems.append(f"No prospect limits for season {d['season']}.")
        else:
            want = {(r["team_abbr"], r["t"]): r["n"] for r in _all(
                conn, """select team_abbr, case when round <= %s then 'BC' else 'DC' end as t, count(*) as n
                           from draft_slots where draft_id = %s and skip_reason is null group by 1, 2""",
                (bc_max["bc_slot_max_round"], draft_id))}
            have = {(r["team_abbr"], r["slot_type"]): r["n"] for r in _all(
                conn, """select team_abbr, slot_type, count(*) as n from prospect_draft_slots
                          where season = %s and filled_upid is null group by 1, 2""", (d["season"],))}
            for key in sorted(set(want) | set(have)):
                if want.get(key, 0) != have.get(key, 0):
                    problems.append(f"{key[0]} has {want.get(key, 0)} {key[1]} pick(s) in the order but "
                                    f"{have.get(key, 0)} open prepaid {key[1]} slot(s).")
    if _one(conn, "select 1 as x from draft_rankings where draft_id = %s limit 1", (draft_id,)):
        for r in _all(conn, """select pl.name, pl.reason from draft_rankings k join draft_pool(%s) pl on pl.upid = k.upid
                                where k.draft_id = %s and not pl.eligible order by k.rank limit 10""", (draft_id, draft_id)):
            problems.append(f"Ranked player {r['name']} is not eligible: {r['reason']}.")
        missing = _all(conn, """select pl.name from draft_pool(%s) pl
                                 where pl.eligible and pl.draft_rank is null order by pl.name limit 10""", (draft_id,))
        if missing:
            n = _one(conn, "select count(*) as n from draft_pool(%s) pl where pl.eligible and pl.draft_rank is null", (draft_id,))["n"]
            problems.append(f"{n} eligible player(s) are missing from the ranking, e.g. " + ", ".join(m["name"] for m in missing) + ".")
        # the FYPD rule for FYPD slots: autopick in rounds 1-2 can only take FYPD players, so the ranking must hold
        # enough of them or the draft would pause at the first FYPD timeout
        fypd_slots = _all(conn, "select round from draft_slots where draft_id = %s and round_type = 'fypd' and skip_reason is null", (draft_id,))
        if d["draft_type"] == "prospect" and fypd_slots:
            have = _one(conn, """select count(*) as n from draft_rankings k join draft_pool(%s, %s) pl on pl.upid = k.upid and pl.eligible
                                  where k.draft_id = %s""", (draft_id, fypd_slots[0]["round"], draft_id))["n"]
            if have < len(fypd_slots):
                problems.append(f"The ranking has {have} FYPD-eligible player(s) for {len(fypd_slots)} FYPD pick(s) "
                                "(rounds 1-2 only take FYPD players), so autopick would run out.")
    return problems


def start(conn, draft_id, actor, now=None, check=True):
    """Open the draft: status live, first slot on the clock, a full clock, on-clock and on-deck notifications."""
    now = _now(now)
    with conn.transaction():
        _lock(conn, draft_id)
        d = _draft(conn, draft_id)
        if d["status"] not in ("setup", "pool_check"):
            raise DraftError(f"Can't start a draft that is {d['status']}.", 409)
        if check:
            problems = preflight(conn, draft_id)
            if problems:
                raise DraftError("Not ready to start: " + " ".join(problems), 409)
        first = _open_slots(conn, draft_id, limit=1)
        if not first:
            raise DraftError("This draft has no draftable slots.", 409)
        _exec(conn, "update drafts set status = 'live' where draft_id = %s", (draft_id,))
        _exec(conn, """insert into draft_clock (draft_id, on_clock_overall, runner_heartbeat_at) values (%s, %s, %s)
                       on conflict (draft_id) do update set on_clock_overall = excluded.on_clock_overall,
                            runner_heartbeat_at = excluded.runner_heartbeat_at""", (draft_id, first[0]["overall"], now))
        d = dict(d, status="live")
        _put_on_clock(conn, d, first[0], now)
        _event(conn, draft_id, "start", actor, None, now)
        _notify_on_clock(conn, d, first[0], now)


def pause(conn, draft_id, actor, reason=None, now=None, stale_seconds=HEARTBEAT_STALE_SECONDS, min_seconds=RESTART_MIN_SECONDS):
    """Pause the clock. The time left is stored as it is, except that outage time is never held against a team:
    if the runner's heartbeat is stale or under 1 second is left, at least min_seconds (60) is stored."""
    now = _now(now)
    with conn.transaction():
        _lock(conn, draft_id)
        d = _draft(conn, draft_id)
        _pause(conn, d, actor, reason or "Paused by an admin", now, stale_seconds=stale_seconds, min_seconds=min_seconds)


def resume(conn, draft_id, actor, now=None, min_seconds=RESTART_MIN_SECONDS):
    now = _now(now)
    with conn.transaction():
        _lock(conn, draft_id)
        d = _draft(conn, draft_id)
        _resume(conn, d, actor, now, min_seconds=min_seconds)


def pause_if_runner_stale(conn, draft_id, now=None, stale_seconds=HEARTBEAT_STALE_SECONDS, min_seconds=RESTART_MIN_SECONDS):
    """The process is up but the clock runner loop stopped (heartbeat older than stale_seconds): pause the draft
    ("Draft clock paused, commissioner notified") and queue an admin_alert. Called by the API when it sees
    state()['clock']['runner_stale']. The clock stored is at least min_seconds, so the outage is not held against
    the team on the clock. Returns True if it paused, False if the draft is not live or the runner is fine."""
    now = _now(now)
    with conn.transaction():
        _lock(conn, draft_id)
        d = _draft(conn, draft_id)
        clock = _clock(conn, draft_id)
        if d["status"] != "live" or clock is None or not _heartbeat_stale(clock, now, stale_seconds):
            return False
        beat = clock["runner_heartbeat_at"]
        _pause(conn, d, SYSTEM, "The clock runner stopped (no heartbeat)", now, detail={"auto": "runner_stale"},
               stale_seconds=stale_seconds, min_seconds=min_seconds)
        _alert(conn, draft_id, "Draft paused: the clock runner stopped ticking "
                               f"(last heartbeat {beat.isoformat() if beat else 'never'}). Check the bot, then resume.", now)
        return True


def _heartbeat_stale(clock, now, stale_seconds):
    return clock["runner_heartbeat_at"] is None or (now - clock["runner_heartbeat_at"]).total_seconds() > stale_seconds


def _pause(conn, d, actor, reason, now, remaining_ms=None, detail=None, stale_seconds=HEARTBEAT_STALE_SECONDS,
           min_seconds=RESTART_MIN_SECONDS):
    if d["status"] != "live":
        raise DraftError(f"Can't pause a draft that is {d['status']}.", 409)
    clock = _clock(conn, d["draft_id"])
    detail = dict(detail or {})
    if remaining_ms is None:
        remaining_ms = max(0, int((clock["deadline_at"] - now).total_seconds() * 1000))
        # Outage time must never become an autopick: if the runner was down (stale heartbeat) or the clock has
        # just run out, the team keeps at least min_seconds when the draft resumes.
        if (_heartbeat_stale(clock, now, stale_seconds) or remaining_ms < 1000) and remaining_ms < min_seconds * 1000:
            detail["raised_from_ms"] = remaining_ms
            remaining_ms = min_seconds * 1000
    _exec(conn, "update draft_clock set deadline_at = null, paused_remaining_ms = %s where draft_id = %s",
          (remaining_ms, d["draft_id"]))
    _exec(conn, "update drafts set status = 'paused' where draft_id = %s", (d["draft_id"],))
    _event(conn, d["draft_id"], "pause", actor, {"reason": reason, "remaining_ms": remaining_ms, **detail}, now)
    _notify(conn, "draft_paused", d["draft_id"], {"reason": reason}, now)


def _resume(conn, d, actor, now, min_seconds=RESTART_MIN_SECONDS):
    if d["status"] != "paused":
        raise DraftError(f"Can't resume a draft that is {d['status']}.", 409)
    clock = _clock(conn, d["draft_id"])
    left = clock["paused_remaining_ms"] or 0
    if left < 1000:         # never resume into an instant autopick
        left = max(left, min_seconds * 1000)
    _exec(conn, """update draft_clock set deadline_at = %s, paused_remaining_ms = null, runner_heartbeat_at = %s
                    where draft_id = %s""", (now + timedelta(milliseconds=left), now, d["draft_id"]))
    _exec(conn, "update drafts set status = 'live' where draft_id = %s", (d["draft_id"],))
    _event(conn, d["draft_id"], "resume", actor, {"remaining_ms": left}, now)
    _notify(conn, "draft_resumed", d["draft_id"], {"message": "Draft resumed", "remaining_ms": left}, now)


# ------------------------------------------------------------------ the clock

def _put_on_clock(conn, d, slot, now, full=True):
    """Put `slot` on the clock with a full clock (forklift teams get the short one). If the draft is
    paused the full length is stored as time remaining instead of a deadline."""
    forklift = _is_forklift(conn, d["draft_id"], slot["team_abbr"])
    secs = d["forklift_seconds"] if forklift else d["clock_seconds"]
    if d["status"] == "paused":
        _exec(conn, """update draft_clock set on_clock_overall = %s, deadline_at = null, paused_remaining_ms = %s, warned = false
                        where draft_id = %s""", (slot["overall"], secs * 1000, d["draft_id"]))
    else:
        _exec(conn, """update draft_clock set on_clock_overall = %s, deadline_at = %s, paused_remaining_ms = null, warned = false
                        where draft_id = %s""", (slot["overall"], now + timedelta(seconds=secs), d["draft_id"]))


def _notify_on_clock(conn, d, slot, now):
    """On-clock DM (one per run of back-to-back picks) and the on-deck DM for the team after the run."""
    upcoming = _open_slots(conn, d["draft_id"], exclude=slot["overall"], limit=8)
    run = _run(slot, upcoming)
    forklift = _is_forklift(conn, d["draft_id"], slot["team_abbr"])
    _notify(conn, "on_clock", d["draft_id"], {
        "overall": slot["overall"], "label": _label(slot), "round": slot["round"], "pick_in_round": slot["pick_in_round"],
        "via": slot["original_team"] if slot["original_team"] != slot["team_abbr"] else None,
        "run_size": len(run), "tag": _tag(len(run)),
        "clock_seconds": d["forklift_seconds"] if forklift else d["clock_seconds"]}, now, team=slot["team_abbr"])
    after = [s for s in upcoming[len(run) - 1:] if s["team_abbr"] != slot["team_abbr"]]
    if after:
        _notify(conn, "on_deck", d["draft_id"], {"overall": after[0]["overall"], "label": _label(after[0])}, now,
                team=after[0]["team_abbr"])


def _advance(conn, d, now):
    """Move the clock to the next open slot, or complete the draft. Returns the slot, or None if complete."""
    nxt = _open_slots(conn, d["draft_id"], limit=1)
    if not nxt:
        _exec(conn, "update draft_clock set on_clock_overall = null, deadline_at = null, paused_remaining_ms = null where draft_id = %s",
              (d["draft_id"],))
        _exec(conn, "update drafts set status = 'complete' where draft_id = %s", (d["draft_id"],))
        _event(conn, d["draft_id"], "complete", SYSTEM, None, now)
        return None
    _put_on_clock(conn, d, nxt[0], now)
    return nxt[0]


def clock_tick(conn, draft_id, now=None, stale_seconds=HEARTBEAT_STALE_SECONDS, restart_seconds=RESTART_MIN_SECONDS):
    """One second of the clock runner. Reads the database, holds nothing in memory. Returns what it did:
    'extended' (restart rule), 'autopick', 'forklift_off', 'paused' (autopick found nobody), 'warned', or None.

    Order: restart rule, heartbeat, then deadline/warning. A restart tick never autopicks: nobody is
    autopicked for time lost to an outage. stale_seconds and restart_seconds default to the spec's 15 and 60;
    only the simulator (2-second clocks) passes other values.
    """
    now = _now(now)
    with conn.transaction():
        _lock(conn, draft_id)
        d = _draft(conn, draft_id)
        clock = _clock(conn, draft_id)
        if d["status"] not in ("live", "paused") or clock is None:
            return None
        stale = _heartbeat_stale(clock, now, stale_seconds)
        _exec(conn, "update draft_clock set runner_heartbeat_at = %s where draft_id = %s", (now, draft_id))
        if d["status"] == "paused":
            return None
        if stale:
            floor = now + timedelta(seconds=restart_seconds)
            if clock["deadline_at"] < floor:
                _exec(conn, "update draft_clock set deadline_at = %s where draft_id = %s", (floor, draft_id))
                _event(conn, draft_id, "clock_extended", SYSTEM, {"to": floor.isoformat(), "was": clock["deadline_at"].isoformat()}, now)
                _notify(conn, "draft_resumed", draft_id,
                        {"message": f"Draft resumed, {restart_seconds // 60}:{restart_seconds % 60:02d} added to the clock"}, now)
                return "extended"
            return None
        if clock["deadline_at"] <= now:
            return _timeout(conn, d, clock, now)
        left = (clock["deadline_at"] - now).total_seconds()
        if left < WARNING_SECONDS and not clock["warned"]:
            slot = _slot(conn, draft_id, clock["on_clock_overall"])
            _exec(conn, "update draft_clock set warned = true where draft_id = %s", (draft_id,))
            if _is_forklift(conn, draft_id, slot["team_abbr"]):
                return None         # forklift teams are autopicked in seconds: no warning
            _notify(conn, "warning", draft_id, {"overall": slot["overall"], "label": _label(slot),
                                                "seconds_left": int(left)}, now, team=slot["team_abbr"])
            return "warned"
    return None


def _autopick_candidate(conn, d, slot, boards_only):
    """Spec order: this pick's board, then 'Any pick', both skipping the ineligible; then (not forklift)
    the uploaded ranking. None if nothing qualifies."""
    r = _one(conn, """select b.upid from draft_boards b
                        join draft_pool(%s, %s) pl on pl.upid = b.upid and pl.eligible
                       where b.season = %s and b.draft_type = %s and b.team_abbr = %s and b.slot_key in (%s, 'any')
                       order by (b.slot_key = 'any'), b.position limit 1""",
             (d["draft_id"], slot["round"], d["season"], d["draft_type"], slot["team_abbr"], f"{d['draft_id']}:{slot['overall']}"))
    if r or boards_only:
        return r["upid"] if r else None
    r = _one(conn, """select k.upid from draft_rankings k
                        join draft_pool(%s, %s) pl on pl.upid = k.upid and pl.eligible
                       where k.draft_id = %s order by k.rank, k.upid limit 1""", (d["draft_id"], slot["round"], d["draft_id"]))
    return r["upid"] if r else None


def _timeout(conn, d, clock, now):
    """The deadline passed. The whole autopick attempt runs in a savepoint: if it fails for ANY reason (a rule,
    a bug, a database error) it is rolled back, the draft is paused and Zach is alerted. The runner never retries
    a failing pick every second."""
    slot = _slot(conn, d["draft_id"], clock["on_clock_overall"])
    team = slot["team_abbr"]
    try:
        with conn.transaction():
            return _autopick_attempt(conn, d, slot, now)
    except Exception as e:  # noqa: BLE001 - DraftError, or anything unexpected: pause and alert, never loop
        if not isinstance(e, DraftError):
            log.exception("autopick for %s at %s failed", team, _label(slot))
        why = str(e) if isinstance(e, DraftError) else f"{type(e).__name__}: {e}"
        _pause(conn, _draft(conn, d["draft_id"]), SYSTEM, f"Autopick failed for {team} at {_label(slot)}: {why}", now,
               remaining_ms=d["clock_seconds"] * 1000)
        _alert(conn, d["draft_id"], f"Draft paused: autopick failed for {team} at {_label(slot)}: {why}", now)
        return "paused"


def _autopick_attempt(conn, d, slot, now):
    team = slot["team_abbr"]
    forklift = _is_forklift(conn, d["draft_id"], team)
    upid = _autopick_candidate(conn, d, slot, boards_only=forklift)
    if upid is None:
        if forklift:   # the board ran dry: forklift turns itself off and the team gets a normal clock
            _exec(conn, "delete from draft_forklift where draft_id = %s and team_abbr = %s", (d["draft_id"], team))
            _exec(conn, "update draft_clock set deadline_at = %s, warned = false where draft_id = %s",
                  (now + timedelta(seconds=d["clock_seconds"]), d["draft_id"]))
            _event(conn, d["draft_id"], "forklift_off", SYSTEM, {"team": team, "reason": "board empty"}, now)
            _notify(conn, "forklift_off", d["draft_id"], {"message": "Forklift is off: your board ran out. You're on a normal clock."},
                    now, team=team)
            return "forklift_off"
        _pause(conn, d, SYSTEM, f"No autopick for {team} at {_label(slot)}: empty board and no eligible ranked player",
               now, remaining_ms=d["clock_seconds"] * 1000)
        _alert(conn, d["draft_id"], f"Draft paused: {team} timed out at {_label(slot)} and there is nobody to autopick "
                                    "(no ranking uploaded, or it is used up). Upload a ranking or pick for them, then resume.", now)
        return "paused"
    source = "forklift" if forklift else "autopick"
    picks = _do_picks(conn, d, team, [upid], source, SYSTEM, now)
    _after_picks(conn, d["draft_id"], picks, now)
    return source


# ------------------------------------------------------------------ picking

def _sign_contract(conn, d, slot, upid, actor, now):
    """Open the contract the round gives, through league.actions. Returns the tier written on the pick."""
    try:
        if d["draft_type"] == "prospect":
            res = actions.assign_prospect_contract(conn, upid, slot["team_abbr"], acquired_via="prospect_draft",
                                                   created_by=actor, season=d["season"], draft_round=slot["round"], now=now)
            return res["prospect_type"]
        res = actions.assign_keeper_draft_pick(conn, upid, slot["team_abbr"], slot["round"], created_by=actor,
                                               season=d["season"], now=now)
        return res["tier"]
    except RuleError as e:
        raise DraftError(str(e), 409) from e


def _do_picks(conn, d, team, upids, source, actor, now):
    """Steps 2-5 of "Picking" for each upid in order, inside the caller's transaction and lock.
    Each pick moves the clock, so a later pick in the list is checked against the next slot on the clock:
    a 2 Time is two picks, each timed from when it went on the clock."""
    picks = []
    for upid in upids:
        d = _draft(conn, d["draft_id"])
        if d["status"] != "live":
            raise DraftError("The draft is paused." if d["status"] == "paused" else f"The draft is not live ({d['status']}).", 409)
        clock = _clock(conn, d["draft_id"])
        if clock["on_clock_overall"] is None:
            raise DraftError("Nobody is on the clock. The draft has no picks left." if picks else "Nobody is on the clock.", 409)
        slot = _slot(conn, d["draft_id"], clock["on_clock_overall"])
        if source != "admin" and team != slot["team_abbr"]:
            raise DraftError(f"It's not your turn. {slot['team_abbr']} is on the clock.", 403)
        pool = _one(conn, "select * from draft_pool(%s, %s) where upid = %s", (d["draft_id"], slot["round"], upid))
        if pool is None:
            raise DraftError("Unknown player.", 404)
        if not pool["eligible"]:
            raise DraftError(pool["reason"], 409)
        if source == "admin":
            used = None                     # an admin pick isn't timed: speed_rows leaves NULL out of the averages
        elif source in ("autopick", "forklift") or _is_forklift(conn, d["draft_id"], slot["team_abbr"]):
            used = d["clock_seconds"]       # full clock, or forklift would win the speed awards every year
        else:
            left = max(0.0, (clock["deadline_at"] - now).total_seconds())
            used = min(d["clock_seconds"], max(0, round(d["clock_seconds"] - left)))
        tier = _sign_contract(conn, d, slot, upid, actor, now)
        _exec(conn, """insert into @picks (draft_id, overall, upid, team_abbr, source, made_by, made_at, seconds_used, contract_tier)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
              (d["draft_id"], slot["overall"], upid, slot["team_abbr"], source, actor, now, used, tier))
        actions._event(conn, upid, "draft_pick",
                       f"{slot['team_abbr']} drafted {pool['name']} with pick {_label(slot)} of {d['draft_id']} ({source}, by {actor}).",
                       team=slot["team_abbr"], season=d["season"], by=actor, ts=now, contract=tier)
        picks.append({"overall": slot["overall"], "label": _label(slot), "upid": upid, "name": pool["name"],
                      "team": slot["team_abbr"], "source": source, "contract": tier, "seconds_used": used})
        _advance(conn, d, now)
    return picks


def _after_picks(conn, draft_id, picks, now):
    """Step 6: pick_made (one channel message for the whole run), then on_clock/on_deck, or completion."""
    team = picks[0]["team"]
    _notify(conn, "pick_made", draft_id, {"team": team, "run_size": len(picks), "tag": _tag(len(picks)), "picks": picks}, now)
    d = _draft(conn, draft_id)
    if d["status"] == "complete":
        _notify(conn, "draft_complete", draft_id, {"message": "The draft is complete."}, now)
        _on_complete(conn, d, now)
        return
    nxt = _slot(conn, draft_id, _clock(conn, draft_id)["on_clock_overall"])
    last = _slot(conn, draft_id, picks[-1]["overall"])
    continues_run = nxt["team_abbr"] == team and nxt["makeup_seq"] is None and last["makeup_seq"] is None
    if not continues_run:           # the rest of a 2 Time / 3 Time was already told by the first on-clock DM
        _notify_on_clock(conn, d, nxt, now)


def make_pick(conn, draft_id, team, upid, source, actor, now=None):
    """The only way a pick happens. `team` is the verified team making the pick; the slot on the clock must
    be theirs unless source is 'admin' (the pick is then made for the team on the clock). Raises DraftError."""
    return make_picks(conn, draft_id, team, [upid], source, actor, now)[0]


def make_picks(conn, draft_id, team, upids, source, actor, now=None):
    """Make several picks for a run (2 Time / 3 Time) in one transaction, in order; all or nothing."""
    now = _now(now)
    if source not in SOURCES:
        raise DraftError(f"Unknown pick source {source!r}.")
    if source in ("autopick", "forklift") and actor != SYSTEM:
        raise DraftError("Only the clock runner makes autopicks.", 403)
    if not upids or len(set(upids)) != len(upids):
        raise DraftError("Name each player once.")
    with conn.transaction():
        _lock(conn, draft_id)
        d = _draft(conn, draft_id)
        picks = _do_picks(conn, d, team, upids, source, actor, now)
        _after_picks(conn, draft_id, picks, now)
    return picks


# ------------------------------------------------------------------ admin corrections

def _reverse_contract(conn, d, pick, now):
    """Undo what a draft pick did to the roster: league.actions.reverse_draft_pick closes the contract, frees the
    prepaid prospect slot and the roster slot. It refuses if the contract has changed since the pick."""
    name = _player_name(conn, pick["upid"])
    expected = ({"expected_prospect_type": pick["contract_tier"]} if d["draft_type"] == "prospect"
                else {"expected_tier": pick["contract_tier"]})
    try:
        actions.reverse_draft_pick(conn, pick["upid"], pick["team_abbr"], created_by=SYSTEM, season=d["season"], now=now,
                                   note=f"pick {pick['overall']} of {d['draft_id']}", **expected)
    except RuleError as e:
        raise DraftError(f"Can't change this pick: {e}. Fix the contract first.", 409) from e


def _require_unpaid(conn, d):
    """Corrections change who owns whom after the awards were computed from the picks: refuse once they are paid.
    (Test drafts never pay, so they are never blocked.)"""
    if d["is_test"]:
        return
    if _one(conn, "select 1 as x from wb_ledger where ref_type = 'draft_speed' and ref_id = %s limit 1", (str(d["season"]),)):
        raise AwardsPaid(f"The {d['season']} speed awards are already paid, so this draft can't be corrected. "
                         "An admin has to make the fix by hand.", 409)


def _reopen(conn, d, now):
    """A completed draft goes back to live. Its runner heartbeat is stale (nothing ticks a complete draft), so it is
    reset to now: otherwise the next tick would think the runner was down and announce 'Draft resumed'."""
    _exec(conn, "update drafts set status = 'live' where draft_id = %s", (d["draft_id"],))
    _exec(conn, "update draft_clock set runner_heartbeat_at = %s where draft_id = %s", (now, d["draft_id"]))
    return dict(d, status="live")


def _need_reason(reason):
    if not (reason and reason.strip()):
        raise DraftError("A correction needs a reason.")


def _announce(conn, draft_id, action, message, now):
    _notify(conn, "correction", draft_id, {"action": action, "message": message}, now)


def undo_last_pick(conn, draft_id, actor, reason, now=None):
    """Delete the latest pick (last one made), close its contract, and put that slot back on the clock with a full clock."""
    now = _now(now)
    _need_reason(reason)
    with conn.transaction():
        _lock(conn, draft_id)
        d = _draft(conn, draft_id)
        _require_unpaid(conn, d)
        pick = _one(conn, "select * from @picks where draft_id = %s order by seq desc limit 1", (draft_id,))
        if not pick:
            raise DraftError("There is no pick to undo.", 409)
        slot = _slot(conn, draft_id, pick["overall"])
        _reverse_contract(conn, d, pick, now)
        _exec(conn, "delete from @picks where draft_id = %s and overall = %s", (draft_id, pick["overall"]))
        if d["status"] == "complete":
            d = _reopen(conn, d, now)
        _put_on_clock(conn, d, slot, now)
        name = _player_name(conn, pick["upid"])
        msg = f"Pick {_label(slot)} ({name}, {pick['team_abbr']}) was undone: {reason}"
        _event(conn, draft_id, "undo", actor, {"overall": pick["overall"], "upid": pick["upid"], "team": pick["team_abbr"], "reason": reason}, now)
        _announce(conn, draft_id, "undo", msg, now)
        _notify_on_clock(conn, d, slot, now)
    return {"overall": pick["overall"], "message": msg}


def swap_player(conn, draft_id, overall, new_upid, actor, reason, now=None):
    """Replace the player on any earlier pick. The old player goes back to the pool; the new one must be
    eligible for that pick's round. Later picks are untouched."""
    now = _now(now)
    _need_reason(reason)
    with conn.transaction():
        _lock(conn, draft_id)
        d = _draft(conn, draft_id)
        _require_unpaid(conn, d)
        pick = _one(conn, "select * from @picks where draft_id = %s and overall = %s", (draft_id, overall))
        if not pick:
            raise DraftError(f"Pick {overall} has not been made.", 409)
        if pick["upid"] == new_upid:
            raise DraftError("That player is already on this pick.")
        slot = _slot(conn, draft_id, overall)
        pool = _one(conn, "select * from draft_pool(%s, %s) where upid = %s", (draft_id, slot["round"], new_upid))
        if pool is None:
            raise DraftError("Unknown player.", 404)
        if not pool["eligible"]:
            raise DraftError(f"Can't swap in {pool['name']}: {pool['reason']}.", 409)
        _reverse_contract(conn, d, pick, now)
        tier = _sign_contract(conn, d, slot, new_upid, actor, now)
        _exec(conn, "update @picks set upid = %s, contract_tier = %s where draft_id = %s and overall = %s",
              (new_upid, tier, draft_id, overall))
        old_name = _player_name(conn, pick["upid"])
        msg = f"Pick {_label(slot)} ({pick['team_abbr']}) changed from {old_name} to {pool['name']}: {reason}"
        _event(conn, draft_id, "swap", actor, {"overall": overall, "old_upid": pick["upid"], "new_upid": new_upid, "reason": reason}, now)
        _announce(conn, draft_id, "swap", msg, now)
    return {"overall": overall, "message": msg}


def void_pick(conn, draft_id, overall, actor, reason, now=None):
    """Remove any earlier pick and return the player to the pool. The slot becomes a makeup pick for its team,
    taken right after the current pick with a full clock. Later picks are untouched."""
    now = _now(now)
    _need_reason(reason)
    with conn.transaction():
        _lock(conn, draft_id)
        d = _draft(conn, draft_id)
        _require_unpaid(conn, d)
        pick = _one(conn, "select * from @picks where draft_id = %s and overall = %s", (draft_id, overall))
        if not pick:
            raise DraftError(f"Pick {overall} has not been made.", 409)
        slot = _slot(conn, draft_id, overall)
        _reverse_contract(conn, d, pick, now)
        _exec(conn, "delete from @picks where draft_id = %s and overall = %s", (draft_id, overall))
        seq = _one(conn, "select coalesce(max(makeup_seq), 0) + 1 as n from draft_slots where draft_id = %s", (draft_id,))["n"]
        _exec(conn, "update draft_slots set makeup_seq = %s where draft_id = %s and overall = %s", (seq, draft_id, overall))
        if d["status"] == "complete":   # nothing is on the clock: the makeup pick goes straight on with a full clock
            d = _reopen(conn, d, now)
            _put_on_clock(conn, d, slot, now)
            _notify_on_clock(conn, d, slot, now)
        name = _player_name(conn, pick["upid"])
        msg = f"Pick {_label(slot)} ({name}, {pick['team_abbr']}) was voided: {reason}. {pick['team_abbr']} gets a makeup pick."
        _event(conn, draft_id, "void", actor, {"overall": overall, "upid": pick["upid"], "team": pick["team_abbr"], "reason": reason}, now)
        _event(conn, draft_id, "makeup_pick", actor, {"overall": overall, "team": pick["team_abbr"], "makeup_seq": seq}, now)
        _announce(conn, draft_id, "void", msg, now)
    return {"overall": overall, "makeup_seq": seq, "message": msg}


def set_forklift(conn, draft_id, team, enabled, actor, now=None):
    """Turn forklift on or off for a team. Turning it on for the team on the clock shortens the running clock."""
    now = _now(now)
    with conn.transaction():
        _lock(conn, draft_id)
        d = _draft(conn, draft_id)
        if d["status"] == "complete":
            raise DraftError("The draft is complete.", 409)
        if enabled:
            _exec(conn, "insert into draft_forklift (draft_id, team_abbr, enabled_at) values (%s, %s, %s) on conflict do nothing",
                  (draft_id, team, now))
            clock = _clock(conn, draft_id)
            if d["status"] == "live" and clock and clock["on_clock_overall"] is not None:
                slot = _slot(conn, draft_id, clock["on_clock_overall"])
                short = now + timedelta(seconds=d["forklift_seconds"])
                if slot["team_abbr"] == team and clock["deadline_at"] > short:
                    _exec(conn, "update draft_clock set deadline_at = %s where draft_id = %s", (short, draft_id))
        else:
            _exec(conn, "delete from draft_forklift where draft_id = %s and team_abbr = %s", (draft_id, team))
        _event(conn, draft_id, "forklift_on" if enabled else "forklift_off", actor, {"team": team}, now)


# ------------------------------------------------------------------ boards and rankings

def set_board(conn, draft_id, team, slot, upids):
    """Replace one of `team`'s big board lists. slot = an overall number of a pick the team owns, or 'any'.
    Up to 50 players, each listed once. Boards are keyed (season, draft_type), so they carry across test drafts."""
    d = _draft(conn, draft_id)
    upids = list(upids)
    if len(upids) > BOARD_MAX:
        raise DraftError(f"A board holds up to {BOARD_MAX} players.")
    if len(set(upids)) != len(upids):
        raise DraftError("A player can be on a list only once.")
    if slot == "any":
        key = "any"
    else:
        s = _slot(conn, draft_id, int(slot))
        if not s or s["team_abbr"] != team:
            raise DraftError("You can only make a list for a pick you own.", 403)
        key = f"{draft_id}:{s['overall']}"
    known = {r["upid"] for r in _all(conn, "select upid from players where upid = any(%s)", (upids,))}
    if known != set(upids):
        raise DraftError(f"Unknown player(s): {sorted(set(upids) - known)}.", 404)
    with conn.transaction():
        _exec(conn, "delete from draft_boards where season = %s and draft_type = %s and team_abbr = %s and slot_key = %s",
              (d["season"], d["draft_type"], team, key))
        for i, u in enumerate(upids, start=1):
            _exec(conn, "insert into draft_boards (season, draft_type, team_abbr, slot_key, position, upid) values (%s, %s, %s, %s, %s, %s)",
                  (d["season"], d["draft_type"], team, key, i, u))


def get_board(conn, draft_id, team):
    """One team's lists: {slot_key: [upid, ...]}. Only ever called with the verified team's own name."""
    d = _draft(conn, draft_id)
    out = {}
    for r in _all(conn, """select slot_key, upid from draft_boards where season = %s and draft_type = %s and team_abbr = %s
                            order by slot_key, position""", (d["season"], d["draft_type"], team)):
        out.setdefault(r["slot_key"], []).append(r["upid"])
    return out


def upload_rankings(conn, draft_id, rows, source=None, dry_run=False, actor=SYSTEM, now=None):
    """Load a ranking. rows = [{'upid' | 'mlb_id' | 'name', 'rank'}]. Matches on upid, then mlb_id, then name
    (accents and case ignored; a name shared by several players only matches if exactly one of them is eligible).
    Returns {'matched': n, 'unmatched': [{row, reason}], 'saved': bool}. Nothing is saved while any row is unmatched
    (fix the file and upload again), and dry_run never saves. A saved upload replaces the old ranking."""
    now = _now(now)
    d = _draft(conn, draft_id)
    matched, unmatched, seen = {}, [], {}
    for row in rows:
        rank = row.get("rank")
        try:
            rank = int(rank)
            if rank < 1:
                raise ValueError
        except (TypeError, ValueError):
            unmatched.append({"row": row, "reason": "rank must be a whole number, 1 or more"})
            continue
        upid, reason = None, None
        if row.get("upid"):
            hit = _one(conn, "select upid from players where upid = %s", (str(row["upid"]),))
            upid, reason = (hit["upid"] if hit else None), "no player with that upid"
        elif row.get("mlb_id"):
            hits = _all(conn, "select upid from players where mlb_id = %s", (int(row["mlb_id"]),))
            if len(hits) == 1:
                upid = hits[0]["upid"]
            else:
                reason = "no player with that MLB ID" if not hits else f"{len(hits)} players share that MLB ID"
        elif row.get("name"):
            hits = _all(conn, "select p.upid, pl.eligible from players p join draft_pool(%s) pl on pl.upid = p.upid "
                              "where draft_norm(p.name) = draft_norm(%s)", (draft_id, row["name"]))
            if len(hits) > 1:
                hits = [h for h in hits if h["eligible"]]
            if len(hits) == 1:
                upid = hits[0]["upid"]
            else:
                reason = "no player with that name" if not hits else "several players have that name; use an ID"
        else:
            reason = "row has no upid, mlb_id or name"
        if upid is None:
            unmatched.append({"row": row, "reason": reason})
        elif upid in seen:
            unmatched.append({"row": row, "reason": f"same player as another row ({seen[upid]})"})
        else:
            seen[upid] = row.get("name") or row.get("mlb_id") or upid
            matched[upid] = rank
    saved = False
    if not unmatched and not dry_run:
        with conn.transaction():
            _lock(conn, draft_id)
            _exec(conn, "delete from draft_rankings where draft_id = %s", (draft_id,))
            for upid, rank in matched.items():
                _exec(conn, "insert into draft_rankings (draft_id, upid, rank, source) values (%s, %s, %s, %s)", (draft_id, upid, rank, source))
            _event(conn, draft_id, "rankings_uploaded", actor, {"rows": len(matched), "source": source}, now)
        saved = True
    return {"matched": len(matched), "unmatched": unmatched, "saved": saved}


def set_override(conn, draft_id, upid, action, reason, actor, now=None):
    """Admin include/exclude from the pool check, with a reason. Shows in draft_events."""
    now = _now(now)
    if action not in ("include", "exclude"):
        raise DraftError("action must be 'include' or 'exclude'.")
    _need_reason(reason)
    with conn.transaction():
        _lock(conn, draft_id)
        _draft(conn, draft_id)
        _exec(conn, """insert into draft_pool_overrides (draft_id, upid, action, reason, set_by, set_at) values (%s, %s, %s, %s, %s, %s)
                       on conflict (draft_id, upid) do update set action = excluded.action, reason = excluded.reason,
                            set_by = excluded.set_by, set_at = excluded.set_at""", (draft_id, upid, action, reason, actor, now))
        _event(conn, draft_id, "override", actor, {"upid": upid, "action": action, "reason": reason}, now)


# ------------------------------------------------------------------ adding a player mid-draft

def request_add_player(conn, draft_id, team, mlb_id, player_name, note=None, now=None):
    """A manager asks for a player to be added. Notifies admins. If this team is on the clock and has not used its one
    auto-pause yet, the draft pauses (and resumes with the same time when an admin decides)."""
    now = _now(now)
    with conn.transaction():
        _lock(conn, draft_id)
        d = _draft(conn, draft_id)
        if d["status"] == "complete":
            raise DraftError("The draft is complete.", 409)
        have = _one(conn, "select upid, name from players where mlb_id = %s order by upid limit 1", (int(mlb_id),))
        if have:
            pool = _one(conn, "select eligible, reason from draft_pool(%s) where upid = %s", (draft_id, have["upid"]))
            why = "he is already in the pool" if pool["eligible"] else f"he is not in the pool: {pool['reason']}"
            raise DraftError(f"{have['name']} is already in FBP ({why}).", 409)
        pause_it = False
        if d["status"] == "live":
            clock = _clock(conn, draft_id)
            slot = _slot(conn, draft_id, clock["on_clock_overall"])
            used = _one(conn, "select 1 as x from draft_add_requests where draft_id = %s and requested_by = %s and paused_draft",
                        (draft_id, team))
            pause_it = slot["team_abbr"] == team and used is None
        try:
            with conn.transaction():
                req = _one(conn, """insert into draft_add_requests (draft_id, requested_by, mlb_id, player_name, note, paused_draft, created_at)
                                    values (%s, %s, %s, %s, %s, %s, %s) returning *""",
                           (draft_id, team, int(mlb_id), player_name, note, pause_it, now))
        except psycopg.errors.UniqueViolation as e:
            raise DraftError(f"Someone already asked for {player_name}; an admin will decide.", 409) from e
        _event(conn, draft_id, "add_requested", team, {"request_id": req["id"], "mlb_id": int(mlb_id), "player": player_name,
                                                       "note": note, "paused_draft": pause_it}, now)
        _notify(conn, "add_request", draft_id, {"audience": "admins", "request_id": req["id"], "player": player_name,
                                                "mlb_id": int(mlb_id), "requested_by": team, "note": note}, now)
        if pause_it:
            _pause(conn, d, SYSTEM, f"{team} asked for a player to be added", now, detail={"auto": "add_request", "request_id": req["id"]})
    return req


def decide_add_request(conn, request_id, approve, actor, decision_note=None, player_type=None, fypd=None, rank=None,
                       details=None, now=None):
    """An admin approves or rejects a request. Approving creates the player (next numeric UPID), puts him in the ranking if
    one exists (rank defaults to after the last ranked player) and he is searchable and draftable at once. `details` may carry
    mlb_team, positions, birth_date from the MLB lookup. Rejecting needs a reason. Resumes the draft if this request paused it."""
    now = _now(now)
    details = details or {}
    peek = _one(conn, "select draft_id from draft_add_requests where id = %s", (request_id,))
    if not peek:
        raise DraftError("No such request.", 404)
    draft_id = peek["draft_id"]
    with conn.transaction():
        _lock(conn, draft_id)
        d = _draft(conn, draft_id)
        req = _one(conn, "select * from draft_add_requests where id = %s for update", (request_id,))
        if req["status"] != "pending":
            raise DraftError(f"That request was already {req['status']}.", 409)
        created = None
        if approve:
            ptype = player_type or ("Farm" if d["draft_type"] == "prospect" else "MLB")
            if ptype not in ("Farm", "MLB"):
                raise DraftError("player_type must be 'Farm' or 'MLB'.")
            _exec(conn, "select pg_advisory_xact_lock(hashtext('players_upid'))")
            if _one(conn, "select 1 as x from players where mlb_id = %s", (req["mlb_id"],)):
                raise DraftError("A player with that MLB ID already exists.", 409)
            upid = str(_one(conn, "select coalesce(max(upid::bigint), 0) + 1 as n from players where upid ~ '^[0-9]+$'")["n"])
            _exec(conn, """insert into players (upid, name, mlb_id, mlb_team, positions, player_type, fypd, birth_date, raw)
                           values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                  (upid, req["player_name"], req["mlb_id"], details.get("mlb_team"), details.get("positions"), ptype,
                   bool(fypd), details.get("birth_date"), Jsonb({"added_by_draft_request": request_id})))
            ranked = _one(conn, "select max(rank) as m from draft_rankings where draft_id = %s", (draft_id,))["m"]
            if rank is not None or ranked is not None:
                _exec(conn, "insert into draft_rankings (draft_id, upid, rank, source) values (%s, %s, %s, 'added mid-draft')",
                      (draft_id, upid, rank if rank is not None else ranked + 1))
            created = upid
        elif not (decision_note and decision_note.strip()):
            raise DraftError("Say why the request is rejected.")
        _exec(conn, """update draft_add_requests set status = %s, decided_by = %s, decided_at = %s, decision_note = %s, created_upid = %s
                        where id = %s""", ("approved" if approve else "rejected", actor, now, decision_note, created, request_id))
        _event(conn, draft_id, "player_added" if approve else "add_rejected", actor,
               {"request_id": request_id, "player": req["player_name"], "upid": created, "note": decision_note}, now)
        text = (f"Added to the pool: {req['player_name']}" if approve else f"Not added: {req['player_name']}. {decision_note}")
        _notify(conn, "add_request_decided", draft_id, {"request_id": request_id, "approved": approve, "message": text}, now,
                team=req["requested_by"])
        if approve:
            _notify(conn, "player_added", draft_id, {"message": text, "upid": created}, now)
        if req["paused_draft"] and d["status"] == "paused":
            last = _one(conn, "select detail from draft_events where draft_id = %s and kind = 'pause' order by id desc limit 1", (draft_id,))
            if last and (last["detail"] or {}).get("request_id") == request_id:   # nobody paused it again since
                _resume(conn, d, SYSTEM, now)
    return _one(conn, "select * from draft_add_requests where id = %s", (request_id,))


# ------------------------------------------------------------------ search

_SEARCH_SQL = """
with q as (select draft_norm(%(q)s) as qn),
     toks as (select t from q, unnest(string_to_array(q.qn, ' ')) as t where t <> ''),
     cand as materialized (
       select p.upid, p.name, p.positions, p.mlb_team, p.raw, p.birth_date, p.rank as player_rank, r.rank as draft_rank, x.tier
         from players p
        cross join q
        cross join lateral (select draft_norm(p.name) as nn) n
         left join draft_rankings r on r.draft_id = %(draft)s and r.upid = p.upid
        cross join lateral (select case
                when n.nn = q.qn then 0
                when not exists (select 1 from toks where not exists
                       (select 1 from unnest(string_to_array(n.nn, ' ')) w where left(w, length(toks.t)) = toks.t)) then 1
                when exists (select 1 from player_aliases a where a.upid = p.upid and (a.alias_normalized = q.qn or
                       not exists (select 1 from toks where not exists
                         (select 1 from unnest(string_to_array(a.alias_normalized, ' ')) w where left(w, length(toks.t)) = toks.t)))) then 2
                when length(q.qn) >= 3 and greatest(similarity(n.nn, q.qn), word_similarity(q.qn, n.nn)) >= 0.45 then 3
              end as tier) x
        where x.tier is not null
        order by x.tier, r.rank nulls last, p.rank nulls last, p.name, p.upid
        limit %(limit)s)
select c.upid, c.name, c.positions, c.mlb_team as org, c.raw->>'level' as level,
       case when c.birth_date is not null then date_part('year', age(c.birth_date))::int
            when c.raw->>'age' ~ '^[0-9]+$' then (c.raw->>'age')::int end as age,
       pl.fypd, c.draft_rank as rank, pl.eligible, pl.reason, c.tier
  from cand c
  join draft_pool(%(draft)s, %(round)s) pl on pl.upid = c.upid
 order by c.tier, c.draft_rank nulls last, c.player_rank nulls last, c.name, c.upid"""


def search(conn, draft_id, q, limit=SEARCH_LIMIT, for_round=None):
    """Type-ahead search over players and player_aliases. Accents and case ignored; first names, last names, partial
    names ('chourio', 'j wood') and aliases match. Ineligible players come back too, with `eligible` false and `reason`.
    Ordered by match quality, then the draft ranking. Starts at 2 characters. Same names are told apart by org/positions."""
    d = _draft(conn, draft_id)
    norm = _one(conn, "select draft_norm(%s) as n", (q,))["n"]
    if len(norm) < 2:
        return []
    if for_round is None:
        clock = _clock(conn, draft_id)
        if clock and clock["on_clock_overall"] is not None:
            for_round = _slot(conn, draft_id, clock["on_clock_overall"])["round"]
    return _all(conn, _SEARCH_SQL, {"q": q, "draft": d["draft_id"], "round": for_round, "limit": limit})


# ------------------------------------------------------------------ state

def _jsonable(v):
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def state(conn, draft_id, now=None, team=None):
    """The payload for GET /api/draft/{id}/state (JSON-ready), with server_now so browsers correct their clocks.
    `team` is the verified team asking: only that team's own board is included, never anyone else's."""
    now = _now(now)
    d = _draft(conn, draft_id)
    clock = _clock(conn, draft_id)
    rows = _all(conn, """select s.*, p.upid as pick_upid, pl.name as pick_name, p.team_abbr as pick_team, p.source as pick_source,
                                p.made_by, p.made_at, p.seconds_used
                           from draft_slots s
                           left join @picks p on p.draft_id = s.draft_id and p.overall = s.overall
                           left join players pl on pl.upid = p.upid
                          where s.draft_id = %s order by s.overall""", (draft_id,))
    # runs of back-to-back draftable picks by one team: 2 Time, 3 Time
    prev, runs = None, []
    for r in rows:
        if r["skip_reason"]:
            continue
        if prev is not None and prev["team_abbr"] == r["team_abbr"] and not r["makeup_seq"] and not prev["makeup_seq"]:
            runs[-1].append(r["overall"])
        else:
            runs.append([r["overall"]])
        prev = r
    run_of = {o: (run[0] == o, len(run), run.index(o) + 1) for run in runs for o in run}
    slots = []
    for r in rows:
        first, size, idx = run_of.get(r["overall"], (False, 1, 1))
        slots.append({"overall": r["overall"], "label": _label(r), "round": r["round"], "pick_in_round": r["pick_in_round"],
                      "team": r["team_abbr"], "original_team": r["original_team"], "round_type": r["round_type"],
                      "skip_reason": r["skip_reason"], "makeup": r["makeup_seq"] is not None,
                      "run_size": size, "run_index": idx, "run_tag": _tag(size),
                      "pick": {"upid": r["pick_upid"], "name": r["pick_name"], "team": r["pick_team"], "source": r["pick_source"],
                               "made_by": r["made_by"], "made_at": r["made_at"], "seconds_used": r["seconds_used"]}
                      if r["pick_upid"] else None})
    out = {"server_now": now, "draft": {k: d[k] for k in ("draft_id", "season", "draft_type", "status", "clock_seconds",
                                                          "forklift_seconds", "starts_at", "is_test")}}
    on_clock, next_teams = None, []
    if clock and clock["on_clock_overall"] is not None:
        slot = _slot(conn, draft_id, clock["on_clock_overall"])
        upcoming = _open_slots(conn, draft_id, exclude=slot["overall"], limit=8)
        run = _run(slot, upcoming)
        on_clock = {"overall": slot["overall"], "label": _label(slot), "team": slot["team_abbr"],
                    "via": slot["original_team"] if slot["original_team"] != slot["team_abbr"] else None,
                    "round": slot["round"], "run_size": len(run), "run_tag": _tag(len(run))}
        next_teams = [s["team_abbr"] for s in upcoming[len(run) - 1:]][:3]
    seconds_left = None
    if clock and clock["deadline_at"] is not None:
        seconds_left = max(0.0, (clock["deadline_at"] - now).total_seconds())
    elif clock and clock["paused_remaining_ms"] is not None:
        seconds_left = clock["paused_remaining_ms"] / 1000
    out["clock"] = {"on_clock_overall": clock["on_clock_overall"] if clock else None,
                    "deadline_at": clock["deadline_at"] if clock else None,
                    "paused_remaining_ms": clock["paused_remaining_ms"] if clock else None,
                    "seconds_left": seconds_left, "warned": clock["warned"] if clock else False,
                    "runner_heartbeat_at": clock["runner_heartbeat_at"] if clock else None,
                    "runner_stale": bool(clock and d["status"] == "live" and (
                        clock["runner_heartbeat_at"] is None or
                        (now - clock["runner_heartbeat_at"]).total_seconds() > HEARTBEAT_STALE_SECONDS))}
    out["on_clock"], out["next_teams"] = on_clock, next_teams
    out["slots"] = slots
    out["ticker"] = [{"overall": s["overall"], "label": s["label"], **s["pick"]} for s in
                     sorted((s for s in slots if s["pick"]), key=lambda s: s["pick"]["made_at"], reverse=True)[:TICKER_SIZE]]
    out["forklift"] = [r["team_abbr"] for r in _all(conn, "select team_abbr from draft_forklift where draft_id = %s order by team_abbr", (draft_id,))]
    out["pending_requests"] = _all(conn, """select id, requested_by, mlb_id, player_name, note, created_at from draft_add_requests
                                             where draft_id = %s and status = 'pending' order by id""", (draft_id,))
    amounts = [r["amount"] for r in _all(conn, "select amount from draft_award_amounts where season = %s order by place", (d["season"],))]
    out["speed"] = speed_standings(speed_rows(conn, d["season"], d["is_test"]), amounts)
    if team:
        out["my_board"] = get_board(conn, draft_id, team)
    return _jsonable(out)


# ------------------------------------------------------------------ speed awards

def speed_rows(conn, season, is_test=False):
    """[(team, total seconds_used, picks)] across every draft of the season (test drafts only count with test drafts)."""
    return [(r["team_abbr"], int(r["s"]), r["n"]) for r in _all(
        conn, """select p.team_abbr, sum(p.seconds_used) as s, count(*) as n
                   from @picks p join drafts d on d.draft_id = p.draft_id
                  where d.season = %s and d.is_test = %s and p.seconds_used is not null group by p.team_abbr""", (season, is_test))]


def speed_standings(rows, amounts):
    """Pure rule. rows = [(team, total_seconds, picks)]; amounts = prize money for places 1..6.
    Fastest average first. Ties share the combined prize of the places they cover, split evenly and rounded
    down to whole WizBucks (the ledger holds whole dollars). Returns [{team, picks, avg, place, amount}].

    ROUNDING LEAVES MONEY UNPAID. The rule is unchanged (split evenly, whole dollars), but when the pool doesn't
    divide evenly the remainder is never paid. Two teams tied for 6th share $5 and get $2 each: $1 is unpaid.
    Two tied for 5th share $10 + $5 and get $7 each: $1 is unpaid. (Three tied for 1st share $75 = $25 each, exact.)
    Zach decides whether the leftover dollar goes to someone, is dropped, or stays as it is. See the build report.
    """
    ranked = sorted(((Fraction(s, n), t, n) for t, s, n in rows if n), key=lambda x: (x[0], x[1]))
    out, i = [], 0
    while i < len(ranked):
        j = i
        while j < len(ranked) and ranked[j][0] == ranked[i][0]:
            j += 1
        pool = sum(amounts[i:j])      # places i+1 .. j; places past the 6th pay nothing
        for avg, team, n in ranked[i:j]:
            out.append({"team": team, "picks": n, "avg": float(avg), "place": i + 1, "amount": pool // (j - i)})
        i = j
    return out


def pay_speed_awards(conn, season, now=None):
    """Pay the fastest-drafter awards once the season's second draft is complete. Real drafts only (never is_test).
    Does nothing until both the Prospect and Keeper drafts are complete, and nothing a second time. One transaction.
    Returns the paid standings ([] if nothing was paid)."""
    now = _now(now)
    with conn.transaction():
        _exec(conn, "select pg_advisory_xact_lock(hashtext(%s))", (f"draft_awards:{season}",))
        done = {r["draft_type"]: r["status"] for r in _all(conn, "select draft_type, status from drafts where season = %s and not is_test", (season,))}
        if done.get("prospect") != "complete" or done.get("keeper") != "complete":
            return []
        if _one(conn, "select 1 as x from wb_ledger where ref_type = 'draft_speed' and ref_id = %s limit 1", (str(season),)):
            return []
        amounts = [r["amount"] for r in _all(conn, "select amount from draft_award_amounts where season = %s order by place", (season,))]
        if not amounts:
            raise DraftError(f"No speed award amounts for season {season} (draft_award_amounts).")
        standings = speed_standings(speed_rows(conn, season, False), amounts)
        phase = actions._current_phase(conn, season, now)
        paid = [s for s in standings if s["amount"] > 0]
        for s in paid:
            actions.wb_credit(conn, s["team"], s["amount"], "draft_award", phase, season, created_by=SYSTEM, ref_type="draft_speed",
                              ref_id=str(season), note=f"Fastest drafter, place {s['place']} (avg {s['avg']:.1f}s over {s['picks']} picks)")
        _notify(conn, "speed_awards", f"season-{season}", {"season": season, "final": True, "standings": standings, "paid": paid}, now)
    return paid


def _on_complete(conn, d, now):
    """A draft just completed: pay the awards if it was the season's second draft, else post the standings so far.
    Payment is its own savepoint: if it fails, the pick that completed the draft still stands and Zach is alerted."""
    if d["is_test"]:
        return
    try:
        with conn.transaction():
            paid = pay_speed_awards(conn, d["season"], now)
    except (DraftError, RuleError, psycopg.Error) as e:
        _alert(conn, d["draft_id"], f"Speed awards for {d['season']} were not paid: {e}. Run pay_speed_awards again after fixing.", now)
        return
    if not paid:
        amounts = [r["amount"] for r in _all(conn, "select amount from draft_award_amounts where season = %s order by place", (d["season"],))]
        _notify(conn, "speed_awards", d["draft_id"], {"season": d["season"], "final": False,
                                                      "standings": speed_standings(speed_rows(conn, d["season"], False), amounts)}, now)


# ------------------------------------------------------------------ importing the 2026 boards

def import_boards_from_app_state(conn, season, draft_type="prospect", target_season=None):
    """Load the 2026 managers' single big boards from the mirror (app_state key manager_boards_<season>:
    {team: [upid, ...]}) into draft_boards as 'any' lists ("Existing boards import as Any pick"). Unknown teams and
    unknown upids are skipped and reported; a list is cut to BOARD_MAX; a team that already has an 'any' list for
    this (season, draft_type) is left alone. The boards land under target_season (default: the same season; pass 2027
    to seed the 2027 draft rooms from the 2026 boards). 2026 had one board per manager, so it goes to one draft_type
    ('prospect' unless told otherwise). Returns {'imported': {team: n}, 'unknown_upids': {team: [...]},
    'unknown_teams': [...], 'skipped_existing': [...]}."""
    row = _one(conn, "select value from app_state where key = %s", (f"manager_boards_{season}",))
    if row is None:
        raise DraftError(f"The mirror has no manager_boards_{season}.", 404)
    target = target_season if target_season is not None else season
    teams = {r["abbr"] for r in _all(conn, "select abbr from teams")}
    out = {"imported": {}, "unknown_upids": {}, "unknown_teams": [], "skipped_existing": []}
    with conn.transaction():
        for team, upids in sorted((row["value"] or {}).items()):
            if team not in teams or not isinstance(upids, list):
                out["unknown_teams"].append(team)
                continue
            seen, ordered = set(), []
            for u in upids:
                u = str(u)
                if u not in seen:
                    seen.add(u)
                    ordered.append(u)
            known = {r["upid"] for r in _all(conn, "select upid from players where upid = any(%s)", (ordered,))}
            bad = [u for u in ordered if u not in known]
            good = [u for u in ordered if u in known][:BOARD_MAX]
            if bad:
                out["unknown_upids"][team] = bad
            if _one(conn, "select 1 as x from draft_boards where season = %s and draft_type = %s and team_abbr = %s and slot_key = 'any' limit 1",
                    (target, draft_type, team)):
                out["skipped_existing"].append(team)
                continue
            for i, u in enumerate(good, start=1):
                _exec(conn, "insert into draft_boards (season, draft_type, team_abbr, slot_key, position, upid) values (%s, %s, %s, 'any', %s, %s)",
                      (target, draft_type, team, i, u))
            out["imported"][team] = len(good)
    return out
