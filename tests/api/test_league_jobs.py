"""Spec 03 scheduler logic: due-ness, ordering and backoff (league/jobs.py).

No database and no Discord. The dispatcher's only database touches are `job_row`,
`_record_success` and `_record_failure`, so these replace those three and the job
runners, leaving the decisions under test on their own.

The jobs' own behaviour (what they write) is covered in tests/league/test_jobs.py,
which needs Postgres.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from league import jobs

ET = ZoneInfo("America/New_York")

MORNING = datetime(2027, 5, 10, 7, 0, tzinfo=ET)      # after NIGHTLY_AFTER_HOUR_ET
PRE_DAWN = datetime(2027, 5, 10, 3, 0, tzinfo=ET)     # before it


class FakeConn:
    """Only the transaction() the dispatcher opens; no queries reach it."""

    def transaction(self):
        return contextlib.nullcontext()


@pytest.fixture
def conn():
    return FakeConn()


@pytest.fixture
def stub(monkeypatch):
    """Replace the three job_runs queries and record what the dispatcher writes."""
    state = {"rows": {}, "success": [], "failure": []}

    monkeypatch.setattr(jobs, "job_row", lambda conn, name: state["rows"].get(name))
    monkeypatch.setattr(
        jobs, "_record_success",
        lambda conn, name, ts, result: state["success"].append((name, result)),
    )

    def _fail(conn, name, ts, error):
        row = state["rows"].get(name) or {"consecutive_failures": 0}
        failures = row["consecutive_failures"] + 1
        state["failure"].append((name, str(error), failures))
        return failures, jobs.backoff_seconds(failures)

    monkeypatch.setattr(jobs, "_record_failure", _fail)
    return state


def _row(*, last_success=None, failures=0, next_attempt=None):
    return {"job_name": "x", "last_run_at": None, "last_success_at": last_success,
            "last_result": None, "consecutive_failures": failures,
            "next_attempt_at": next_attempt, "last_error": None}


# ----------------------------------------------------------------- the ladder

def test_the_backoff_ladder_matches_the_warp_md_reference_implementation(health_mod):
    """WARP.md points at health._standings_backoff_interval_seconds as the fix that must
    not be bypassed. The job ladder has to be the same one, not a second opinion."""
    for failures in range(0, 40):
        assert jobs.backoff_seconds(failures) == \
            health_mod._standings_backoff_interval_seconds(failures), failures


def test_the_ladder_widens_and_caps():
    assert jobs.backoff_seconds(0) == 0
    assert jobs.backoff_seconds(3) == 30 * 60
    assert jobs.backoff_seconds(6) == 60 * 60
    assert jobs.backoff_seconds(12) == 4 * 60 * 60
    assert jobs.backoff_seconds(1000) == 4 * 60 * 60   # capped, never unbounded


# -------------------------------------------------------------------- due-ness

def test_a_job_in_backoff_is_not_due(conn, stub):
    stub["rows"][jobs.JOB_PROSPECT_STATUS] = _row(failures=5, next_attempt=MORNING + timedelta(minutes=20))

    assert jobs.is_due(conn, jobs.JOB_PROSPECT_STATUS, MORNING) is False


def test_backoff_expiring_makes_it_due_again(conn, stub):
    stub["rows"][jobs.JOB_PROSPECT_STATUS] = _row(failures=5, next_attempt=MORNING - timedelta(minutes=1))

    assert jobs.is_due(conn, jobs.JOB_PROSPECT_STATUS, MORNING) is True


def test_a_nightly_job_already_run_today_is_not_due(conn, stub):
    stub["rows"][jobs.JOB_ROSTER_CHECK] = _row(last_success=MORNING - timedelta(hours=1))

    assert jobs.is_due(conn, jobs.JOB_ROSTER_CHECK, MORNING) is False


def test_a_nightly_job_run_yesterday_is_due(conn, stub):
    stub["rows"][jobs.JOB_ROSTER_CHECK] = _row(last_success=MORNING - timedelta(days=1))

    assert jobs.is_due(conn, jobs.JOB_ROSTER_CHECK, MORNING) is True


def test_a_nightly_job_waits_until_the_morning(conn, stub):
    """Nightly jobs hold off until 6am ET so the prior day's MLB stats are final."""
    assert jobs.is_due(conn, jobs.JOB_PROSPECT_STATUS, PRE_DAWN) is False


def test_a_never_run_nightly_job_is_due_in_the_morning(conn, stub):
    assert jobs.is_due(conn, jobs.JOB_PROSPECT_STATUS, MORNING) is True


def test_a_window_driven_job_ignores_the_clock(conn, stub):
    """phase_close and installments are driven by window dates, so they are checked on
    every pass -- including before dawn and more than once a day."""
    stub["rows"][jobs.JOB_PHASE_CLOSE] = _row(last_success=PRE_DAWN - timedelta(minutes=5))

    assert jobs.is_due(conn, jobs.JOB_PHASE_CLOSE, PRE_DAWN, nightly=False) is True


def test_a_window_driven_job_still_respects_backoff(conn, stub):
    stub["rows"][jobs.JOB_INSTALLMENTS] = _row(failures=4, next_attempt=MORNING + timedelta(hours=1))

    assert jobs.is_due(conn, jobs.JOB_INSTALLMENTS, MORNING, nightly=False) is False


# ------------------------------------------------------------------ dispatcher

def test_phase_close_runs_before_installments(conn, stub, monkeypatch):
    """close_phase_rollover refuses once the next phase's installment is credited, so
    the order is load-bearing, not cosmetic."""
    order = []
    monkeypatch.setattr(jobs, "RUNNERS", {
        name: (lambda c, ctx, _n=name: order.append(_n) or f"{_n} ok")
        for name in jobs.JOB_ORDER
    })

    jobs.run_due_jobs(conn, now=MORNING)

    assert order == list(jobs.JOB_ORDER)
    assert order.index(jobs.JOB_PHASE_CLOSE) < order.index(jobs.JOB_INSTALLMENTS)


def test_one_failing_job_does_not_stop_the_others(conn, stub, monkeypatch):
    def boom(c, ctx):
        raise RuntimeError("statsapi timed out")

    monkeypatch.setattr(jobs, "RUNNERS", {
        **{name: (lambda c, ctx, _n=name: f"{_n} ok") for name in jobs.JOB_ORDER},
        jobs.JOB_PROSPECT_STATUS: boom,
    })

    report = jobs.run_due_jobs(conn, now=MORNING)

    assert "statsapi timed out" in report[jobs.JOB_PROSPECT_STATUS]["error"]
    assert report[jobs.JOB_PROSPECT_STATUS]["retry_in_seconds"] == 0  # first failure
    assert report[jobs.JOB_ROSTER_CHECK]["message"] == "roster_check ok"
    assert [n for n, _, _ in stub["failure"]] == [jobs.JOB_PROSPECT_STATUS]
    assert len(stub["success"]) == len(jobs.JOB_ORDER) - 1


def test_repeated_failure_widens_the_retry_interval(conn, stub, monkeypatch):
    """The point of the whole ladder: never a fixed short interval forever."""
    stub["rows"][jobs.JOB_PROSPECT_STATUS] = _row(failures=5)

    def boom(c, ctx):
        raise RuntimeError("still down")

    monkeypatch.setattr(jobs, "RUNNERS", {jobs.JOB_PROSPECT_STATUS: boom})

    report = jobs.run_due_jobs(conn, now=MORNING, only=[jobs.JOB_PROSPECT_STATUS])

    assert report[jobs.JOB_PROSPECT_STATUS]["consecutive_failures"] == 6
    assert report[jobs.JOB_PROSPECT_STATUS]["retry_in_seconds"] == 60 * 60


def test_a_job_that_is_not_due_is_reported_and_not_run(conn, stub, monkeypatch):
    stub["rows"][jobs.JOB_ROSTER_CHECK] = _row(last_success=MORNING - timedelta(hours=2))
    ran = []
    monkeypatch.setattr(jobs, "RUNNERS", {
        name: (lambda c, ctx, _n=name: ran.append(_n) or "ok") for name in jobs.JOB_ORDER
    })

    report = jobs.run_due_jobs(conn, now=MORNING, only=[jobs.JOB_ROSTER_CHECK])

    assert report[jobs.JOB_ROSTER_CHECK] == {"ran": False, "reason": "not due"}
    assert ran == []


def test_only_limits_which_jobs_are_considered(conn, stub, monkeypatch):
    ran = []
    monkeypatch.setattr(jobs, "RUNNERS", {
        name: (lambda c, ctx, _n=name: ran.append(_n) or "ok") for name in jobs.JOB_ORDER
    })

    jobs.run_due_jobs(conn, now=MORNING, only=[jobs.JOB_GRADUATION])

    assert ran == [jobs.JOB_GRADUATION]


def test_the_prospect_job_refuses_without_a_fetcher(conn, stub):
    """It must fail loudly into backoff, not silently skip the refresh."""
    report = jobs.run_due_jobs(conn, now=MORNING, only=[jobs.JOB_PROSPECT_STATUS])

    assert "fetcher" in report[jobs.JOB_PROSPECT_STATUS]["error"]
