"""Shared setup for the API wiring tests.

`health.py` is a module with side effects: it reads BOT_API_KEY into a module
global, `sys.exit(1)`s when DISCORD_TOKEN is unset, and prints emoji at import
time (which a Windows cp1252 console cannot encode). All three are dealt with
here, before any test module imports it.

None of these tests need a database: every one of them replaces
`fbp_db.connection` and the `auction.service` functions, so they assert the
wiring (flag, headers, error mapping, scheduler branches) and nothing else.
Spec 02's service tests 1-7 cover the transactions themselves.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # same pattern as scripts/db/*.py
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("DISCORD_TOKEN", "test-discord-token")
os.environ.setdefault("BOT_API_KEY", "test-api-key")

for _stream in (sys.stdout, sys.stderr):
    try:  # Windows: health.py prints emoji while importing
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

API_KEY = os.environ["BOT_API_KEY"]
AUTH = {"X-API-Key": API_KEY}


class FakeConn:
    """Stand-in for a psycopg connection. Touching it at all is a test failure."""

    def __getattr__(self, name):  # pragma: no cover - only hit on a real query
        raise AssertionError(f"the wiring must not use the connection directly (.{name})")


@pytest.fixture
def health_mod():
    import health

    return health


@pytest.fixture
def client(health_mod):
    from fastapi.testclient import TestClient

    # No lifespan handlers in health.py, so no context manager needed.
    return TestClient(health_mod.app)


@pytest.fixture
def flag_on(monkeypatch):
    """Turn the Spec 02 Postgres auction path on for one test."""
    monkeypatch.setenv("FBP_AUCTION_DB", "1")


@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.delenv("FBP_AUCTION_DB", raising=False)


@pytest.fixture
def no_commits(monkeypatch, health_mod):
    """Record every _commit_and_push call instead of running git."""
    calls = []
    monkeypatch.setattr(health_mod, "_commit_and_push", lambda *a, **k: calls.append((a, k)))
    return calls


@pytest.fixture
def no_discord(monkeypatch, health_mod):
    """Swallow the after-commit Discord posts."""
    monkeypatch.setattr(health_mod, "_schedule_bot_coro", lambda coro: coro.close())
    monkeypatch.setattr(health_mod, "_send_auction_log_message", lambda *a, **k: _Noop())
    return None


class _Noop:
    def close(self):
        pass


@pytest.fixture
def fake_db(monkeypatch):
    """Replace fbp_db.connection with one that yields a FakeConn."""
    import contextlib

    import fbp_db

    opened = []

    @contextlib.contextmanager
    def _connection():
        conn = FakeConn()
        opened.append(conn)
        yield conn

    monkeypatch.setattr(fbp_db, "connection", _connection)
    return opened


@pytest.fixture
def no_db(monkeypatch):
    """Any attempt to open a connection is a test failure."""
    import fbp_db

    def _boom():
        raise AssertionError("the flag is off: nothing may open a database connection")

    monkeypatch.setattr(fbp_db, "connection", _boom)
