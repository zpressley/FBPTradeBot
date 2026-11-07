"""Spec 02's "One scheduler": the auction jobs on the bot's auction_tick loop.

These drive `Auction._auction_tick_db` with a fake clock and a stand-in `self`,
so no Discord connection, no cog construction and no database are involved. The
point of every test is idempotence: the database is the guard, so a second tick
in the same minute must change nothing.

Dates: 2026-04-13 is a Monday inside the 2026 auction calendar in
config/season_dates.json, so Sunday 2026-04-19 10:00 ET is that week's
`processing` phase and 09:59 is still `ob_final`.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

ET = ZoneInfo("America/New_York")
MONDAY = date(2026, 4, 13)
SUN_10 = datetime(2026, 4, 19, 10, 0, tzinfo=ET)
SUN_0959 = datetime(2026, 4, 19, 9, 59, tzinfo=ET)
SUN_11 = datetime(2026, 4, 19, 11, 0, tzinfo=ET)
MON_15 = datetime(2026, 4, 13, 15, 0, tzinfo=ET)


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, content):
        self.sent.append(content)


class FakeCog:
    """Only the attributes `_auction_tick_db` actually touches."""

    def __init__(self):
        self._ob_open_week = None
        self._db_alert_week = None
        self.ob_alerts = 0
        self.auction_channel = FakeChannel()
        self.admin_channel = FakeChannel()

    async def _send_ob_open_alert(self):
        self.ob_alerts += 1

    async def _get_auction_channel(self):
        return self.auction_channel

    async def _get_admin_log_channel(self):
        return self.admin_channel

    def _admin_mention(self):
        return "<@admin>"


@pytest.fixture
def tick():
    from commands.auction import Auction

    def _run(cog, now):
        return asyncio.run(Auction._auction_tick_db(cog, now))

    return _run


@pytest.fixture
def stub_service(monkeypatch):
    """Replace the auction service and the connection; record every call."""
    import contextlib

    import fbp_db
    from auction import service

    @contextlib.contextmanager
    def _connection():
        yield object()

    monkeypatch.setattr(fbp_db, "connection", _connection)

    calls = {"get_board": [], "resolve_week": [], "mark_announced": []}
    state = {"board": {}, "resolve": {}, "resolve_raises": None}

    def fake_get_board(conn, now, calendar, team=None, priority_source=None):
        calls["get_board"].append(now)
        return state["board"]

    def fake_resolve_week(conn, week_start, resolved_by, now, calendar=None, force=False):
        calls["resolve_week"].append(
            {"week_start": week_start, "resolved_by": resolved_by, "force": force}
        )
        if state["resolve_raises"] is not None:
            raise state["resolve_raises"]
        return state["resolve"]

    def fake_mark_announced(conn, week_start):
        calls["mark_announced"].append(week_start)
        return True

    monkeypatch.setattr(service, "get_board", fake_get_board)
    monkeypatch.setattr(service, "resolve_week", fake_resolve_week)
    monkeypatch.setattr(service, "mark_announced", fake_mark_announced)
    return calls, state


def test_sunday_10am_resolves_the_week_once_as_the_scheduler(tick, stub_service):
    calls, state = stub_service
    state["board"] = {"week_status": "open", "announced_at": None}
    state["resolve"] = {"already_resolved": False, "announce": "Week resolved. WIZ won Doe for $20."}
    cog = FakeCog()

    tick(cog, SUN_10)

    assert len(calls["resolve_week"]) == 1
    assert calls["resolve_week"][0]["week_start"] == MONDAY
    assert calls["resolve_week"][0]["resolved_by"] == "scheduler"
    assert calls["resolve_week"][0]["force"] is False
    assert cog.auction_channel.sent == ["Week resolved. WIZ won Doe for $20."]
    assert calls["mark_announced"] == [MONDAY]


def test_sunday_0959_does_not_resolve(tick, stub_service):
    """9:59am is still ob_final: the OB manager can still Match or Forfeit."""
    calls, state = stub_service
    state["board"] = {"week_status": "open", "announced_at": None}
    cog = FakeCog()

    tick(cog, SUN_0959)

    assert calls["resolve_week"] == []
    assert calls["get_board"] == []  # it returns before reading anything
    assert cog.auction_channel.sent == []


def test_a_resolved_and_announced_week_is_left_alone(tick, stub_service):
    calls, state = stub_service
    state["board"] = {"week_status": "resolved", "announced_at": datetime(2026, 4, 19, 10, 1, tzinfo=ET)}
    cog = FakeCog()

    tick(cog, SUN_10)
    tick(cog, SUN_10)  # a second tick in the same minute

    assert calls["resolve_week"] == []
    assert calls["mark_announced"] == []
    assert cog.auction_channel.sent == []


def test_resolved_but_never_announced_retries_only_the_announcement(tick, stub_service):
    """Spec 02: "the scheduler retries only the announcement, never the resolution"."""
    calls, state = stub_service
    state["board"] = {"week_status": "resolved", "announced_at": None}
    state["resolve"] = {"already_resolved": True, "announce": "Week resolved. Nothing sold."}
    cog = FakeCog()

    tick(cog, SUN_10)

    # resolve_week is how the stored summary is read back, and it writes nothing
    # for an already-resolved week.
    assert len(calls["resolve_week"]) == 1
    assert cog.auction_channel.sent == ["Week resolved. Nothing sold."]
    assert calls["mark_announced"] == [MONDAY]


def test_a_week_with_no_row_is_a_no_op(tick, stub_service):
    """Nobody bid, so there is no auction_weeks row to resolve."""
    calls, state = stub_service
    state["board"] = {"week_status": None, "announced_at": None}
    cog = FakeCog()

    tick(cog, SUN_10)

    assert calls["resolve_week"] == []
    assert cog.auction_channel.sent == []


def test_monday_3pm_posts_the_ob_window_alert_once_per_week(tick, stub_service):
    calls, state = stub_service
    cog = FakeCog()

    tick(cog, MON_15)
    tick(cog, MON_15)

    assert cog.ob_alerts == 1
    assert cog._ob_open_week == MONDAY.isoformat()
    assert calls["resolve_week"] == []  # Monday 3pm is ob_window, not processing


def test_a_failed_resolve_alerts_the_admin_once_after_11am(tick, stub_service):
    calls, state = stub_service
    state["board"] = {"week_status": "open", "announced_at": None}
    state["resolve_raises"] = RuntimeError("balance check failed")
    cog = FakeCog()

    tick(cog, SUN_11)
    tick(cog, SUN_11)

    assert len(calls["resolve_week"]) == 2       # it keeps trying
    assert len(cog.admin_channel.sent) == 1      # but only alerts once
    assert "<@admin>" in cog.admin_channel.sent[0]
    assert "2026-04-13" in cog.admin_channel.sent[0]
    assert cog.auction_channel.sent == []


def test_a_failed_resolve_before_11am_does_not_alert(tick, stub_service):
    calls, state = stub_service
    state["board"] = {"week_status": "open", "announced_at": None}
    state["resolve_raises"] = RuntimeError("transient")
    cog = FakeCog()

    tick(cog, SUN_10)

    assert cog.admin_channel.sent == []


def test_admin_mention_comes_from_managers_json():
    """The alert tags the admin team from config/managers.json (role = admin)."""
    from commands.auction import Auction

    mention = Auction._admin_mention(None)

    assert mention.startswith("<@") and mention.endswith(">")
