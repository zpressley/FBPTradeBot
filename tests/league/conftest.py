"""Fixtures for the Spec 03 action tests (need the local Postgres via DATABASE_URL).

Every test runs inside one outer transaction that is rolled back at the end, so the
shadow mirror and the live Spec 03 tables stay clean. Test 12 is the exception (it needs
committed rows and two real connections) and cleans up after itself.
"""

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="session")
def db_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set")
    host = urlparse(url).hostname or ""
    if host not in ("localhost", "127.0.0.1", "::1") and not os.environ.get("FBP_TEST_ALLOW_REMOTE"):
        pytest.skip(f"refusing to run league tests against non-local database {host!r} "
                    "(set FBP_TEST_ALLOW_REMOTE=1 to override)")
    return url


def check_balances(conn):
    """Spec test 1: every team's balance equals the sum of its ledger rows, and nobody is
    negative without a penalty row."""
    with conn.cursor() as cur:
        cur.execute("""select t.abbr, coalesce(b.balance, 0), coalesce(s.total, 0)
                         from teams t
                         left join wb_balances b on b.team_abbr = t.abbr
                         left join (select team_abbr, sum(amount) as total from wb_ledger group by 1) s
                                on s.team_abbr = t.abbr""")
        for abbr, balance, total in cur.fetchall():
            assert balance == total, f"{abbr}: wb_balances {balance} != sum(amount) {total}"
        cur.execute("""select team_abbr from wb_balances where balance < 0
                          and team_abbr not in (select team_abbr from wb_ledger where kind = 'penalty')""")
        assert cur.fetchall() == [], "negative balance without a penalty"


@pytest.fixture
def conn(db_url):
    c = psycopg.connect(db_url, autocommit=False)
    c.execute("select 1")  # open the outer transaction so every action nests as a savepoint
    try:
        yield c
        check_balances(c)  # spec test 1, "always": after every test
    finally:
        c.rollback()
        c.close()
