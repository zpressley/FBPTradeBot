"""Spec 02 auction route wiring (specs/02_auction_portal.md, "API").

These cover what Spec 02's numbered test list does not reach: the feature flag,
the X-FBP-Team identity rule, the error mapping, and -- route level -- test 7,
"GET /api/auction/current 100 times: zero writes". The row-count half of test 7
and tests 1-6 live in auction/tests/test_service.py and need a database.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tests.api.conftest import AUTH


# --------------------------------------------------------------------- flag off

def test_flag_off_never_opens_a_database_connection(client, flag_off, no_db, no_commits, monkeypatch, health_mod):
    """With FBP_AUCTION_DB unset the legacy JSON path runs, untouched."""
    sentinel = {"week_start": "2026-04-13", "phase": "ob_window", "legacy": True}

    class FakeManager:
        auction_file = "data/auction_current.json"

        def _load_json(self, _path):
            return dict(sentinel)

        def _load_or_initialize_auction(self, _now):
            return dict(sentinel)

        def get_current_phase(self, _now):
            class P:
                value = "ob_window"

            return P()

        def _save_auction_state(self, _state):  # pragma: no cover - must not be reached
            raise AssertionError("phase already matches; no save expected")

    monkeypatch.setattr(health_mod, "AuctionManager", FakeManager)

    resp = client.get("/api/auction/current", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json()["legacy"] is True
    assert no_commits == []  # phase unchanged, so the legacy path commits nothing


# ------------------------------------------------- flag on: GET is read only

def test_flag_on_get_is_read_only_and_commits_nothing(
    client, flag_on, fake_db, no_commits, monkeypatch
):
    """Spec 02 test 7 and bug 4, at the route: 100 reads, zero writes, no git."""
    from auction import service

    calls = []

    def fake_get_board(conn, now, calendar, team=None, priority_source=None):
        calls.append({"now": now, "calendar": calendar, "team": team,
                      "priority_source": priority_source})
        return {"week_start": "2026-04-13", "phase": "ob_window", "prospects": []}

    monkeypatch.setattr(service, "get_board", fake_get_board)

    for _ in range(100):
        resp = client.get("/api/auction/current", headers=AUTH)
        assert resp.status_code == 200

    assert len(calls) == 100
    assert no_commits == []
    assert isinstance(calls[0]["now"], datetime) and calls[0]["now"].tzinfo is not None
    assert set(calls[0]["calendar"]) >= {
        "start", "all_star_break_start", "restart", "playoffs_start"
    }
    assert calls[0]["team"] is None  # no X-FBP-Team header was sent
    assert callable(calls[0]["priority_source"])


def test_flag_on_get_includes_the_team_when_the_header_is_present(
    client, flag_on, fake_db, no_commits, monkeypatch
):
    from auction import service

    seen = {}

    def fake_get_board(conn, now, calendar, team=None, priority_source=None):
        seen["team"] = team
        return {"week_start": "2026-04-13", "phase": "ob_window", "prospects": []}

    monkeypatch.setattr(service, "get_board", fake_get_board)

    resp = client.get("/api/auction/current", headers={**AUTH, "X-FBP-Team": "wiz"})

    assert resp.status_code == 200
    assert seen["team"] == "WIZ"  # normalised, and taken from the header


# --------------------------------------------- flag on: identity from the Worker

def test_flag_on_bid_team_comes_from_the_header_not_the_body(
    client, flag_on, fake_db, no_commits, no_discord, monkeypatch
):
    """Spec 02: team identity comes from the Worker's verified login, never the body."""
    from auction import service

    seen = {}

    def fake_place_bid(conn, team, upid, bid_type, amount, source, now, calendar, **kw):
        seen.update(team=team, upid=upid, bid_type=bid_type, amount=amount, source=source)
        return {"ok": True, "team": team}

    monkeypatch.setattr(service, "place_bid", fake_place_bid)

    resp = client.post(
        "/api/auction/bid",
        headers={**AUTH, "X-FBP-Team": "WIZ"},
        json={"team": "HAM", "prospect_id": "u1", "amount": 25, "bid_type": "OB"},
    )

    assert resp.status_code == 200
    assert seen["team"] == "WIZ"   # not HAM, which the body asked for
    assert seen["source"] == "web"
    assert seen["amount"] == 25
    assert no_commits == []


def test_flag_on_bid_without_a_team_header_is_401(client, flag_on, fake_db, monkeypatch):
    from auction import service

    monkeypatch.setattr(
        service, "place_bid",
        lambda *a, **k: pytest.fail("no team header: the service must not be called"),
    )

    resp = client.post(
        "/api/auction/bid",
        headers=AUTH,
        json={"team": "HAM", "prospect_id": "u1", "amount": 25, "bid_type": "OB"},
    )

    assert resp.status_code == 401


@pytest.mark.parametrize("key", [None, "wrong-key"])
def test_flag_on_team_header_needs_a_valid_api_key(client, flag_on, fake_db, monkeypatch, key):
    from auction import service

    monkeypatch.setattr(
        service, "place_bid",
        lambda *a, **k: pytest.fail("bad API key: the service must not be called"),
    )

    headers = {"X-FBP-Team": "WIZ"}
    if key is not None:
        headers["X-API-Key"] = key

    resp = client.post(
        "/api/auction/bid",
        headers=headers,
        json={"team": "WIZ", "prospect_id": "u1", "amount": 25, "bid_type": "OB"},
    )

    assert resp.status_code == 401


def test_flag_on_team_header_is_not_trusted_via_the_test_mode_bypass(
    client, flag_on, fake_db, monkeypatch, health_mod
):
    """verify_api_key lets an unauthenticated request through when DraftCommands
    .TEST_MODE is on. The team header must NOT ride on that: Spec 02 trusts it
    only behind a valid X-API-Key."""
    from auction import service

    class FakeCog:
        TEST_MODE = True

    class FakeBot:
        def get_cog(self, _name):
            return FakeCog()

    monkeypatch.setattr(health_mod, "bot", FakeBot())
    monkeypatch.setattr(
        service, "place_bid",
        lambda *a, **k: pytest.fail("TEST_MODE must not grant a team identity"),
    )

    resp = client.post(
        "/api/auction/bid",
        headers={"X-FBP-Team": "WIZ"},  # no X-API-Key at all
        json={"team": "WIZ", "prospect_id": "u1", "amount": 25, "bid_type": "OB"},
    )

    assert resp.status_code == 401


# ------------------------------------------------------------- error mapping

def test_flag_on_bid_error_is_400(client, flag_on, fake_db, monkeypatch):
    from auction import service
    from auction.rules import BidError

    def boom(*a, **k):
        raise BidError("Minimum originating bid is $10.")

    monkeypatch.setattr(service, "place_bid", boom)

    resp = client.post(
        "/api/auction/bid",
        headers={**AUTH, "X-FBP-Team": "WIZ"},
        json={"team": "WIZ", "prospect_id": "u1", "amount": 5, "bid_type": "OB"},
    )

    assert resp.status_code == 400
    assert "Minimum originating bid" in resp.json()["detail"]


def test_flag_on_not_admin_is_403(client, flag_on, fake_db, monkeypatch):
    """NotAdmin subclasses BidError, so the mapper has to test it first."""
    from auction import service
    from auction.service import NotAdmin

    def boom(*a, **k):
        raise NotAdmin("Only an admin can do that.")

    monkeypatch.setattr(service, "admin_remove_bid", boom)

    resp = client.post(
        "/api/admin/auction/bids/remove",
        headers={**AUTH, "X-FBP-Team": "HAM"},
        json={"admin": "HAM", "bid_id": "00000000-0000-0000-0000-000000000000"},
    )

    assert resp.status_code == 403


# ------------------------------------------------------------- admin specifics

def test_flag_on_admin_add_bid_uses_the_verified_team_as_the_actor(
    client, flag_on, fake_db, monkeypatch
):
    from auction import service

    seen = {}

    def fake_place_bid(conn, team, upid, bid_type, amount, source, now, calendar, **kw):
        seen.update(team=team, source=source, actor=kw.get("actor"))
        return {"ok": True}

    monkeypatch.setattr(service, "place_bid", fake_place_bid)

    resp = client.post(
        "/api/admin/auction/bids/add",
        headers={**AUTH, "X-FBP-Team": "WIZ"},
        json={"admin": "SPOOF", "team": "HAM", "prospect_id": "u1",
              "amount": 20, "bid_type": "CB"},
    )

    assert resp.status_code == 200
    assert seen["team"] == "HAM"     # the bid is placed FOR this team
    assert seen["actor"] == "WIZ"    # but the admin is the verified caller, not "SPOOF"
    assert seen["source"] == "admin"


def test_flag_on_update_amount_is_refused(client, flag_on, fake_db):
    """Spec 02's service has no edit-amount action, and remove + add here would be
    two transactions. The route says so rather than inventing an admin action."""
    resp = client.post(
        "/api/admin/auction/bids/update-amount",
        headers={**AUTH, "X-FBP-Team": "WIZ"},
        json={"admin": "WIZ", "bid_id": "00000000-0000-0000-0000-000000000000", "amount": 30},
    )

    assert resp.status_code == 501
    assert "Remove the bid" in resp.json()["detail"]
