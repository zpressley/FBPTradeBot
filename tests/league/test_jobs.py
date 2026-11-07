"""Spec 03 "Automatic jobs": what the jobs write (league/jobs.py, league/actions.py).

Needs the local Postgres with 0001-0008 applied; skips without DATABASE_URL, like the
rest of tests/league. Each test runs in the conftest's rolled-back transaction.

The dispatcher's own logic (due-ness, ordering, backoff) is in
tests/api/test_league_jobs.py and needs no database.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from league import actions as A
from league import jobs
from league.actions import RuleError
from tests.league.test_actions import (
    S,
    BY,
    bal,
    contract,
    ledger_count,
    mk_keeper,
    mk_prospect,
    utc,
)


def set_mlb_id(conn, upid, mlb_id):
    conn.execute("update players set mlb_id = %s where upid = %s", (mlb_id, upid))


def status_row(conn, upid):
    with conn.cursor() as cur:
        cur.execute("""select mlb_debut, pa, ip, appearances, age, ab,
                              over_mlb_limits, over_fbp_limits
                         from prospect_status where upid = %s""", (upid,))
        r = cur.fetchone()
    keys = "mlb_debut pa ip appearances age ab over_mlb over_fbp".split()
    return dict(zip(keys, r)) if r else None


def queue_rows(conn, team=None):
    with conn.cursor() as cur:
        cur.execute("""select id, team_abbr, rule, over_by, nights_standing, penalty_amount,
                              cleared_at, confirmed_at, dismissed_at, ledger_id
                         from roster_violation_queue
                        where %s::text is null or team_abbr = %s
                        order by rule""", (team, team))
        rows = cur.fetchall()
    keys = ("id team rule over_by nights penalty cleared_at confirmed_at "
            "dismissed_at ledger_id").split()
    return [dict(zip(keys, r)) for r in rows]


def over_27_active(conn, team="WIZ"):
    """The same shape as spec test 11: 27 active with 9 SP and no swing pitcher."""
    n = 0
    for pos, count in (("SP", 9), ("RP", 5), ("C", 1), ("OF", 12)):
        for _ in range(count):
            mk_keeper(conn, f"test-job-ros-{n}", team, "TC-1")
            conn.execute("update players set positions = %s where upid = %s", (pos, f"test-job-ros-{n}"))
            n += 1
    assert n == 27


# ------------------------------------------------------ prospect_status refresh

def test_refresh_inserts_and_flags_both_limit_sets(conn):
    mk_prospect(conn, "test-job-p1", "WIZ", "DC")
    set_mlb_id(conn, "test-job-p1", 700001)

    res = A.refresh_prospect_status(conn, [{
        "mlb_id": 700001, "mlb_debut": date(2027, 4, 5), "age": 22,
        "ab": 200, "pa": 400, "ip": None, "appearances": None,
    }], season=S, now=utc(2027, 5, 10))

    assert res["updated"] == 1
    assert res["new_debuts"] == ["test-job-p1"]
    row = status_row(conn, "test-job-p1")
    assert row["mlb_debut"] == date(2027, 4, 5)
    assert row["pa"] == 400 and row["ab"] == 200
    assert row["over_fbp"] is True      # 400 PA > prospect_limits.pa 350
    assert row["over_mlb"] is True      # 200 AB > mlb_rookie_limits.ab 130


def test_refresh_under_the_limits_flags_neither(conn):
    mk_prospect(conn, "test-job-p2", "WIZ", "PC")
    set_mlb_id(conn, "test-job-p2", 700002)

    A.refresh_prospect_status(conn, [{
        "mlb_id": 700002, "mlb_debut": None, "age": 20,
        "ab": 10, "pa": 12, "ip": 3.0, "appearances": 2,
    }], season=S, now=utc(2027, 5, 10))

    row = status_row(conn, "test-job-p2")
    assert row["over_fbp"] is False and row["over_mlb"] is False
    assert row["mlb_debut"] is None


def test_refresh_never_clears_a_stored_debut(conn):
    """The API omitting mlb_debut is not the same as the player not having debuted."""
    mk_prospect(conn, "test-job-p3", "WIZ", "DC")
    set_mlb_id(conn, "test-job-p3", 700003)
    A.refresh_prospect_status(conn, [{"mlb_id": 700003, "mlb_debut": date(2027, 4, 5), "age": 23,
                                      "ab": 1, "pa": 1, "ip": None, "appearances": None}],
                              season=S, now=utc(2027, 5, 10))

    res = A.refresh_prospect_status(conn, [{"mlb_id": 700003, "mlb_debut": None, "age": 23,
                                            "ab": 5, "pa": 6, "ip": None, "appearances": None}],
                                    season=S, now=utc(2027, 5, 11))

    assert res["new_debuts"] == []            # not reported a second time
    row = status_row(conn, "test-job-p3")
    assert row["mlb_debut"] == date(2027, 4, 5)
    assert row["pa"] == 6                      # the rest did update


def test_refresh_reports_mlb_ids_that_match_no_player(conn):
    res = A.refresh_prospect_status(conn, [{"mlb_id": 999999999, "mlb_debut": None, "age": 20,
                                            "ab": None, "pa": None, "ip": None, "appearances": None}],
                                    season=S, now=utc(2027, 5, 10))

    assert res["updated"] == 0 and res["unmatched_mlb_ids"] == [999999999]


def test_refresh_twice_with_the_same_rows_changes_nothing(conn):
    mk_prospect(conn, "test-job-p4", "WIZ", "DC")
    set_mlb_id(conn, "test-job-p4", 700004)
    rows = [{"mlb_id": 700004, "mlb_debut": date(2027, 4, 5), "age": 22,
             "ab": 20, "pa": 25, "ip": None, "appearances": None}]

    A.refresh_prospect_status(conn, rows, season=S, now=utc(2027, 5, 10))
    first = status_row(conn, "test-job-p4")
    res = A.refresh_prospect_status(conn, rows, season=S, now=utc(2027, 5, 10))

    assert res["new_debuts"] == []
    assert status_row(conn, "test-job-p4") == first


# ------------------------------------------------------- roster violation queue

def test_reconcile_queues_violations_and_charges_nothing(conn):
    over_27_active(conn)
    before = ledger_count(conn)

    res = A.reconcile_roster_queue(conn, season=S, now=utc(2027, 5, 10))

    rows = {r["rule"]: r for r in queue_rows(conn, "WIZ")}
    assert set(rows) == {"active_max", "sp_max"}
    assert rows["sp_max"]["penalty"] == 20          # pre-filled from wb_prices
    assert rows["active_max"]["nights"] == 1
    assert res["opened"] == 2
    # Spec 03 test 11: nothing is charged until an admin confirms
    assert ledger_count(conn) == before and bal(conn, "WIZ") == 0


def test_reconcile_twice_in_one_night_is_one_row_and_one_night(conn):
    over_27_active(conn)
    A.reconcile_roster_queue(conn, season=S, now=utc(2027, 5, 10, 16))

    res = A.reconcile_roster_queue(conn, season=S, now=utc(2027, 5, 10, 20))

    rows = queue_rows(conn, "WIZ")
    assert len(rows) == 2                           # not four
    assert {r["nights"] for r in rows} == {1}       # same ET day, so no bump
    assert res["opened"] == 0 and res["standing"] == 2


def test_a_violation_standing_three_nights_is_one_row(conn):
    over_27_active(conn)
    for day in (10, 11, 12):
        A.reconcile_roster_queue(conn, season=S, now=utc(2027, 5, day, 16))

    rows = {r["rule"]: r for r in queue_rows(conn, "WIZ")}
    assert len(rows) == 2
    assert rows["active_max"]["nights"] == 3
    assert ledger_count(conn, "WIZ") == 0


def test_a_cleared_violation_is_closed(conn):
    over_27_active(conn)
    A.reconcile_roster_queue(conn, season=S, now=utc(2027, 5, 10))
    # a swing pitcher raises the SP cap, so sp_max clears while active_max stands
    conn.execute("update players set positions = 'SP,RP' where upid = 'test-job-ros-0'")

    res = A.reconcile_roster_queue(conn, season=S, now=utc(2027, 5, 11))

    rows = {r["rule"]: r for r in queue_rows(conn, "WIZ")}
    assert rows["sp_max"]["cleared_at"] is not None
    assert rows["active_max"]["cleared_at"] is None
    assert res["cleared"] == 1


def test_confirming_charges_through_penalty_and_records_the_ledger_row(conn):
    over_27_active(conn)
    A.reconcile_roster_queue(conn, season=S, now=utc(2027, 5, 10))
    qid = {r["rule"]: r for r in queue_rows(conn, "WIZ")}["active_max"]["id"]

    res = A.confirm_roster_violation(conn, qid, created_by="zach", now=utc(2027, 5, 10))

    assert res["charged"] == 20
    assert bal(conn, "WIZ") == -20            # a penalty may overdraw
    row = {r["id"]: r for r in queue_rows(conn, "WIZ")}[qid]
    assert row["confirmed_at"] is not None and row["ledger_id"] == res["ledger_id"]


def test_confirming_twice_is_refused(conn):
    over_27_active(conn)
    A.reconcile_roster_queue(conn, season=S, now=utc(2027, 5, 10))
    qid = {r["rule"]: r for r in queue_rows(conn, "WIZ")}["active_max"]["id"]
    A.confirm_roster_violation(conn, qid, created_by="zach", now=utc(2027, 5, 10))

    with pytest.raises(RuleError, match="already confirmed"):
        A.confirm_roster_violation(conn, qid, created_by="zach", now=utc(2027, 5, 10))


def test_the_system_cannot_confirm_a_violation(conn):
    """penalty() refuses created_by='system', and the queue must not route around it."""
    over_27_active(conn)
    A.reconcile_roster_queue(conn, season=S, now=utc(2027, 5, 10))
    qid = {r["rule"]: r for r in queue_rows(conn, "WIZ")}["active_max"]["id"]

    with pytest.raises(RuleError, match="confirmed by an admin"):
        A.confirm_roster_violation(conn, qid, created_by="system", now=utc(2027, 5, 10))


def test_dismissing_writes_no_ledger_row(conn):
    over_27_active(conn)
    A.reconcile_roster_queue(conn, season=S, now=utc(2027, 5, 10))
    qid = {r["rule"]: r for r in queue_rows(conn, "WIZ")}["sp_max"]["id"]
    before = ledger_count(conn)

    A.dismiss_roster_violation(conn, qid, created_by="zach", reason="swing pitcher on the IL")

    row = {r["id"]: r for r in queue_rows(conn, "WIZ")}[qid]
    assert row["dismissed_at"] is not None and row["ledger_id"] is None
    assert ledger_count(conn) == before
    with pytest.raises(RuleError, match="no open roster violation"):
        A.dismiss_roster_violation(conn, qid, created_by="zach", reason="again")


def test_dismissing_needs_a_reason(conn):
    over_27_active(conn)
    A.reconcile_roster_queue(conn, season=S, now=utc(2027, 5, 10))
    qid = queue_rows(conn, "WIZ")[0]["id"]

    with pytest.raises(RuleError, match="needs a reason"):
        A.dismiss_roster_violation(conn, qid, created_by="zach", reason="")


# --------------------------------------------------- installment ordering guard

def test_the_kap_installment_waits_for_the_pad_rollover(conn):
    """The conftest opens PAD and KAP at the same time, which is exactly the overlap
    that would otherwise deadlock close_phase_rollover for good: once KAP's installment
    is credited it refuses the PAD rollover permanently."""
    assert jobs._rollover_pending(conn, S, "KAP") == "PAD"
    # PRIOR_SEASON is a rollover cap row but not a window we schedule, so PAD is free
    assert jobs._rollover_pending(conn, S, "PAD") is None


def test_once_the_pad_rollover_has_run_the_kap_installment_is_released(conn):
    A.close_phase_rollover(conn, S, "PAD", "KAP")

    assert jobs._rollover_pending(conn, S, "KAP") is None


def test_installments_report_apa_and_ptda_as_unscheduled(conn):
    """They have amounts in wb_installments but no date anywhere -- open question for
    Zach. The job must say so, not invent one."""
    out = jobs.run_installments(conn, {"season": S, "now": utc(2027, 5, 10, 16)})

    assert "APA (no date defined" in out and "PTDA (no date defined" in out


# ------------------------------------------------------- through the dispatcher

def test_the_roster_check_job_records_its_run(conn):
    over_27_active(conn)
    now = utc(2027, 5, 10, 16)   # noon ET, past NIGHTLY_AFTER_HOUR_ET

    report = jobs.run_due_jobs(conn, now=now, season=S, only=[jobs.JOB_ROSTER_CHECK])

    assert report[jobs.JOB_ROSTER_CHECK]["ran"] is True
    assert "nothing charged" in report[jobs.JOB_ROSTER_CHECK]["message"]
    row = jobs.job_row(conn, jobs.JOB_ROSTER_CHECK)
    assert row["consecutive_failures"] == 0 and row["last_success_at"] == now
    assert len(queue_rows(conn, "WIZ")) == 2


def test_the_roster_check_job_is_not_due_twice_the_same_day(conn):
    over_27_active(conn)
    jobs.run_due_jobs(conn, now=utc(2027, 5, 10, 16), season=S, only=[jobs.JOB_ROSTER_CHECK])

    report = jobs.run_due_jobs(conn, now=utc(2027, 5, 10, 20), season=S, only=[jobs.JOB_ROSTER_CHECK])

    assert report[jobs.JOB_ROSTER_CHECK] == {"ran": False, "reason": "not due"}


def test_a_failing_job_records_backoff_in_the_database(conn, monkeypatch):
    def boom(c, ctx):
        raise RuntimeError("statsapi unreachable")

    monkeypatch.setitem(jobs.RUNNERS, jobs.JOB_ROSTER_CHECK, boom)
    now = utc(2027, 5, 10, 16)

    jobs.run_due_jobs(conn, now=now, season=S, only=[jobs.JOB_ROSTER_CHECK])

    row = jobs.job_row(conn, jobs.JOB_ROSTER_CHECK)
    assert row["consecutive_failures"] == 1
    assert "statsapi unreachable" in row["last_error"]
    assert row["last_success_at"] is None


def test_the_debut_watch_job_queues_one_notice_per_prospect(conn):
    mk_prospect(conn, "test-job-d1", "WIZ", "DC")
    set_mlb_id(conn, "test-job-d1", 700010)
    A.refresh_prospect_status(conn, [{"mlb_id": 700010, "mlb_debut": date(2027, 5, 9), "age": 22,
                                      "ab": 2, "pa": 3, "ip": None, "appearances": None}],
                              season=S, now=utc(2027, 5, 10))
    now = utc(2027, 5, 10, 16)

    jobs.run_due_jobs(conn, now=now, season=S, only=[jobs.JOB_DEBUT_WATCH])
    with conn.cursor() as cur:
        cur.execute("""select count(*) from notifications
                        where kind = 'debut_fee_due' and payload->>'upid' = 'test-job-d1'""")
        first = cur.fetchone()[0]

    # a second night must not queue it again
    jobs.run_due_jobs(conn, now=utc(2027, 5, 11, 16), season=S, only=[jobs.JOB_DEBUT_WATCH])
    with conn.cursor() as cur:
        cur.execute("""select count(*) from notifications
                        where kind = 'debut_fee_due' and payload->>'upid' = 'test-job-d1'""")
        second = cur.fetchone()[0]

    assert first == 1 and second == 1
    assert contract(conn, "test-job-d1")["valid_to"] is None   # inside the 7-day window
