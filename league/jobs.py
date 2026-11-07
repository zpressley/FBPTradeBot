"""Spec 03's "Automatic jobs", as plain functions (specs/03_rosters_contracts_wizbucks.md).

`commands/league_jobs.py` is a thin Discord cog that calls `run_due_jobs` once a loop;
everything that decides anything lives here, so the jobs are testable without a bot.

Three rules shape this module:

- **Idempotent.** Every job is safe to run twice, because the database is the guard, not
  an in-memory flag. The actions in `league/actions.py` already check their own ledger
  rows (`credit_installment`, `close_phase_rollover`, `retain_bc`), and the jobs that
  notify check the `notifications` outbox for a row they already queued.
- **Backoff, not retry.** WARP.md's "External API Rate Limits" rule: a job that keeps
  failing must widen its interval, never hammer. The ladder below mirrors
  `health._standings_backoff_interval_seconds`, and `job_runs.next_attempt_at` holds it
  in the database so a Railway redeploy cannot reset it -- which is what made the
  2026 Yahoo incident last 11 days.
- **Nothing is sent from inside a transaction.** Jobs queue rows in `notifications`
  (the Spec 04 outbox). The sender is separate and not built yet, so these jobs enqueue
  and nothing goes out until it exists.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from league import actions
from league.actions import RuleError

ET = ZoneInfo("America/New_York")

#: Nightly jobs wait until this ET hour, so the prior day's MLB stats are final.
NIGHTLY_AFTER_HOUR_ET = 6

JOB_PROSPECT_STATUS = "prospect_status"
JOB_DEBUT_WATCH = "debut_watch"
JOB_GRADUATION = "graduation"
JOB_PHASE_CLOSE = "phase_close"
JOB_INSTALLMENTS = "installments"
JOB_ROSTER_CHECK = "roster_check"

#: Order matters: close_phase_rollover refuses to run once the next phase's installment
#: has been credited (it would count the installment as carried money), so phase_close
#: always runs before installments. tests/league keeps that guard honest.
JOB_ORDER = (
    JOB_PROSPECT_STATUS,
    JOB_DEBUT_WATCH,
    JOB_GRADUATION,
    JOB_PHASE_CLOSE,
    JOB_INSTALLMENTS,
    JOB_ROSTER_CHECK,
)


# --------------------------------------------------------------------- backoff

def backoff_seconds(consecutive_failures: int) -> int:
    """Minimum seconds before a failing job is attempted again.

    Same ladder as health._standings_backoff_interval_seconds: a few blips retry at the
    normal cadence, sustained failure backs off hard and caps at 4 hours.
    """
    if consecutive_failures < 3:
        return 0
    if consecutive_failures < 6:
        return 30 * 60
    if consecutive_failures < 12:
        return 60 * 60
    return 4 * 60 * 60


# ------------------------------------------------------------------- job_runs

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


def job_row(conn, name):
    return _one(conn, "select * from job_runs where job_name = %s", (name,))


def _record_success(conn, name, ts, result):
    _exec(conn, """insert into job_runs (job_name, last_run_at, last_success_at, last_result,
                                         consecutive_failures, next_attempt_at, last_error)
                   values (%s, %s, %s, %s, 0, null, null)
                   on conflict (job_name) do update set
                     last_run_at = excluded.last_run_at, last_success_at = excluded.last_success_at,
                     last_result = excluded.last_result, consecutive_failures = 0,
                     next_attempt_at = null, last_error = null""",
          (name, ts, ts, (result or "")[:2000]))


def _record_failure(conn, name, ts, error):
    row = job_row(conn, name)
    failures = (row["consecutive_failures"] if row else 0) + 1
    wait = backoff_seconds(failures)
    nxt = ts + _timedelta(seconds=wait) if wait else None
    _exec(conn, """insert into job_runs (job_name, last_run_at, consecutive_failures,
                                         next_attempt_at, last_error)
                   values (%s, %s, %s, %s, %s)
                   on conflict (job_name) do update set
                     last_run_at = excluded.last_run_at,
                     consecutive_failures = excluded.consecutive_failures,
                     next_attempt_at = excluded.next_attempt_at,
                     last_error = excluded.last_error""",
          (name, ts, failures, nxt, str(error)[:2000]))
    return failures, wait


def _timedelta(**kw):
    from datetime import timedelta

    return timedelta(**kw)


def _backing_off(row, now) -> bool:
    return bool(row and row["next_attempt_at"] and now < row["next_attempt_at"])


def _ran_today(row, now) -> bool:
    last = row["last_success_at"] if row else None
    return bool(last and last.astimezone(ET).date() >= now.astimezone(ET).date())


def is_due(conn, name, now, *, nightly=True) -> bool:
    """Whether a job should run now: not backing off, and (for nightly jobs) not already
    done today in ET and past NIGHTLY_AFTER_HOUR_ET."""
    row = job_row(conn, name)
    if _backing_off(row, now):
        return False
    if not nightly:
        return True
    if now.astimezone(ET).hour < NIGHTLY_AFTER_HOUR_ET:
        return False
    return not _ran_today(row, now)


# -------------------------------------------------------------- notifications

def _already_queued(conn, kind, upid, season) -> bool:
    return bool(_one(conn, """select 1 as x from notifications
                               where kind = %s and payload->>'upid' = %s
                                 and payload->>'season' = %s""",
                     (kind, str(upid), str(season))))


def _queue(conn, kind, team, payload):
    """One row in the Spec 04 outbox. Nothing is sent from here."""
    _exec(conn, "insert into notifications (kind, team_abbr, payload) values (%s, %s, %s)",
          (kind, team, Jsonb(payload)))


# ------------------------------------------------------------------- the jobs

def run_prospect_status(conn, ctx) -> str:
    """Nightly refresh of prospect_status from the MLB Stats API.

    The fetch happens OUTSIDE the write transaction (it is a network call), and a
    failure propagates so the dispatcher records backoff.
    """
    if ctx.get("fetch_prospect_stats") is None:
        raise RuleError("no MLB Stats API fetcher was provided")
    mlb_ids = [r["mlb_id"] for r in _all(conn, """
        select distinct p.mlb_id from contracts c join players p using (upid)
         where c.valid_to is null and c.kind = 'prospect' and p.mlb_id is not null
         order by p.mlb_id""")]
    if not mlb_ids:
        return "no prospects under contract; nothing to refresh."
    rows = ctx["fetch_prospect_stats"]([int(m) for m in mlb_ids])
    res = actions.refresh_prospect_status(conn, rows, season=ctx["season"], now=ctx["now"])
    return res["message"]


def run_debut_watch(conn, ctx) -> str:
    """DM the owner when a DC prospect debuts, then void the ones past the fee window.

    The DM is queued once per prospect per season: the outbox is the guard, so this is
    safe to run every night. Voiding is actions.void_overdue_dcs, which already skips
    September debuts (Spec 03 test 7).
    """
    season, now = ctx["season"], ctx["now"]
    due = _all(conn, """select c.upid, c.team_abbr, s.mlb_debut, pl.name
                          from contracts c
                          join prospect_status s using (upid)
                          join players pl on pl.upid = c.upid
                         where c.valid_to is null and c.kind = 'prospect' and c.prospect_type = 'DC'
                           and s.mlb_debut is not null
                         order by c.upid""")
    queued = 0
    for r in due:
        if _already_queued(conn, "debut_fee_due", r["upid"], season):
            continue
        deadline = r["mlb_debut"] + _timedelta(days=actions.DEBUT_FEE_WINDOW_DAYS)
        exempt = r["mlb_debut"].month == actions.FEE_EXEMPT_MONTH
        _queue(conn, "debut_fee_due", r["team_abbr"], {
            "upid": r["upid"], "season": season, "player": r["name"],
            "debut": r["mlb_debut"].isoformat(), "deadline": deadline.isoformat(),
            "fee": actions._price(conn, season, "debut_fee"), "september_exempt": exempt,
        })
        queued += 1
    voided = actions.void_overdue_dcs(conn, season=season, now=now)
    return f"{queued} debut fee notice(s) queued; {voided['message']}"


def run_graduation(conn, ctx) -> str:
    """DM prospects who became graduation-eligible, and force the break graduations.

    At the All-Star break everyone over the FBP limits graduates (Spec 03 test 8 covers
    the tiers). Before the break it only notifies.
    """
    season, now = ctx["season"], ctx["now"]
    limits = _one(conn, "select * from prospect_limits where season = %s", (season,))
    if not limits:
        raise RuleError(f"no prospect limits for season {season}")
    rows = _all(conn, """select c.upid, c.team_abbr, pl.name, s.pa, s.ip, s.appearances, s.age
                           from contracts c
                           join prospect_status s using (upid)
                           join players pl on pl.upid = c.upid
                          where c.valid_to is null and c.kind = 'prospect'
                          order by c.upid""")
    break_day = ctx.get("all_star_break_start")
    at_break = bool(break_day and now.astimezone(ET).date() >= break_day)

    queued, graduated = 0, []
    for r in rows:
        over = actions.over_fbp_limits(r, limits)
        if not over:
            continue
        if at_break:
            actions.graduate(conn, r["upid"], created_by="system", season=season,
                             forced=True, now=now)
            graduated.append(r["name"])
        elif not _already_queued(conn, "graduation_eligible", r["upid"], season):
            _queue(conn, "graduation_eligible", r["team_abbr"], {
                "upid": r["upid"], "season": season, "player": r["name"], "over": over,
            })
            queued += 1
    if at_break:
        return f"All-Star break: graduated {len(graduated)} prospect(s): {', '.join(graduated) or 'none'}."
    return f"{queued} graduation notice(s) queued."


def run_phase_close(conn, ctx) -> str:
    """Run the rollover for every window that has closed.

    PAD closing rolls into KAP, KAP into APA (the transitions seeded in
    wb_rollover_caps). close_phase_rollover is idempotent per team, so a window that
    closed days ago is a no-op after the first run.
    """
    season, now = ctx["season"], ctx["now"]
    done = []
    for from_phase, to_phase in (("PAD", "KAP"), ("KAP", "APA")):
        w = _one(conn, """select closes_at from league_windows
                           where season = %s and window_name = %s""", (season, from_phase))
        if not w or now < w["closes_at"]:
            continue
        res = actions.close_phase_rollover(conn, season, from_phase, to_phase)
        if res["teams"]:
            done.append(res["message"])
    return "; ".join(done) if done else "no window has closed with a rollover outstanding."


def _rollover_pending(conn, season, to_phase):
    """The phase whose rollover into `to_phase` has not run yet, or None.

    close_phase_rollover refuses once the next phase's installment is credited, because
    the installment would be counted as carried money -- and that refusal is permanent.
    So if PAD and KAP are open at the same time, crediting KAP first would deadlock the
    PAD rollover forever. This is the guard that stops it.

    from_phases that are not league_windows names (PRIOR_SEASON) are not scheduled here
    and are skipped.
    """
    for cap in _all(conn, """select from_phase from wb_rollover_caps
                              where season = %s and to_phase = %s""", (season, to_phase)):
        frm = cap["from_phase"]
        if not _one(conn, """select 1 as x from league_windows
                              where season = %s and window_name = %s""", (season, frm)):
            continue
        ref = f"{season}:{frm}>{to_phase}"
        done = _one(conn, """select 1 as x from wb_ledger
                              where kind = 'rollover_in' and ref_type = 'rollover' and ref_id = %s
                              limit 1""", (ref,))
        if not done:
            return frm
    return None


def run_installments(conn, ctx) -> str:
    """Credit an installment once its date has arrived.

    PAD and KAP are credited when their league_windows row OPENS -- the simplest reading
    of the spec's "on their calendar dates", and flagged for Zach to confirm since it
    decides when managers actually see the money.

    APA and PTDA have amounts in wb_installments but no date anywhere: league_windows
    only knows PAD, KAP, BC_FINALIZATION and TRADE, and no config file carries one. They
    are reported as unscheduled rather than guessed at.
    """
    season, now = ctx["season"], ctx["now"]
    credited, unscheduled, waiting = [], [], []
    for installment in ("PAD", "KAP"):
        w = _one(conn, """select opens_at from league_windows
                           where season = %s and window_name = %s""", (season, installment))
        if not w:
            unscheduled.append(f"{installment} (no window row)")
            continue
        if now < w["opens_at"]:
            continue
        pending = _rollover_pending(conn, season, installment)
        if pending:
            waiting.append(f"{installment} (waiting on the {pending} rollover)")
            continue
        res = actions.credit_installment(conn, season, installment)
        if any(t["amount"] or t["reward"] for t in res["teams"]):
            credited.append(res["message"])
    for installment in ("APA", "PTDA"):
        unscheduled.append(f"{installment} (no date defined -- needs Zach)")

    msg = "; ".join(credited) if credited else "nothing to credit."
    if waiting:
        msg += f" Held back: {', '.join(waiting)}."
    return f"{msg} Unscheduled: {', '.join(unscheduled)}."


def run_roster_check(conn, ctx) -> str:
    """Nightly roster check into the admin queue. Charges nothing (Spec 03 test 11)."""
    return actions.reconcile_roster_queue(conn, season=ctx["season"], now=ctx["now"])["message"]


RUNNERS = {
    JOB_PROSPECT_STATUS: run_prospect_status,
    JOB_DEBUT_WATCH: run_debut_watch,
    JOB_GRADUATION: run_graduation,
    JOB_PHASE_CLOSE: run_phase_close,
    JOB_INSTALLMENTS: run_installments,
    JOB_ROSTER_CHECK: run_roster_check,
}

#: phase_close and installments are driven by window dates, not a nightly clock, so they
#: are checked on every pass (both are idempotent and cheap when there is nothing to do).
NIGHTLY = {
    JOB_PROSPECT_STATUS: True,
    JOB_DEBUT_WATCH: True,
    JOB_GRADUATION: True,
    JOB_PHASE_CLOSE: False,
    JOB_INSTALLMENTS: False,
    JOB_ROSTER_CHECK: True,
}


def run_due_jobs(conn, *, now=None, season=None, fetch_prospect_stats=None,
                 all_star_break_start=None, only=None) -> dict:
    """Run every job that is due, in JOB_ORDER. Returns one entry per job attempted.

    Each job gets its own transaction, so one failing job neither rolls back nor blocks
    the others; its failure is recorded in job_runs with the backoff interval. The
    connection must be idle/autocommit (see fbp_db.connection).
    """
    now = now or datetime.now(timezone.utc)
    ctx = {"now": now, "season": season, "fetch_prospect_stats": fetch_prospect_stats,
           "all_star_break_start": all_star_break_start}
    report = {}
    for name in JOB_ORDER:
        if only and name not in only:
            continue
        if not is_due(conn, name, now, nightly=NIGHTLY[name]):
            report[name] = {"ran": False, "reason": "not due"}
            continue
        try:
            with conn.transaction():
                message = RUNNERS[name](conn, ctx)
                _record_success(conn, name, now, message)
            report[name] = {"ran": True, "message": message}
        except Exception as exc:                      # noqa: BLE001 - recorded, not swallowed
            with conn.transaction():
                failures, wait = _record_failure(conn, name, now, exc)
            report[name] = {"ran": True, "error": str(exc), "consecutive_failures": failures,
                            "retry_in_seconds": wait}
    return report
