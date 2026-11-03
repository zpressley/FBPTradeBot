"""scripts/db/cutover_trades.py against a throwaway database (the script commits for real)."""

import os
import subprocess
import sys
import uuid
from urllib.parse import urlparse

import psycopg
import pytest

from tests.db.conftest import REPO_ROOT, db_url  # noqa: F401


@pytest.fixture(scope="module")
def scratch_url(db_url):  # noqa: F811
    name = "fbp_trades_cut_" + uuid.uuid4().hex[:8]
    scratch = urlparse(db_url)._replace(path="/" + name).geturl()
    admin = psycopg.connect(psycopg.conninfo.make_conninfo(db_url, dbname="postgres"), autocommit=True)
    admin.execute(f'create database "{name}"')
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL_DIRECT"}
    env["DATABASE_URL"] = scratch
    try:
        for script in ("migrate.py", "import_json.py"):
            r = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "db" / script)], cwd=REPO_ROOT, env=env,
                               capture_output=True, text=True, timeout=300)
            assert r.returncode == 0, r.stdout + r.stderr
        yield scratch
    finally:
        admin.execute(f'drop database if exists "{name}" with (force)')
        admin.close()


def run(url, *args):
    return subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "db" / "cutover_trades.py"), "--database-url", url, *args],
                          cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)


def test_cutover_trades(scratch_url):
    with psycopg.connect(scratch_url) as c:
        mirror = c.execute("select count(*) from trades").fetchone()[0]
        transfers = c.execute("select count(*) from trade_transfers").fetchone()[0]
    r = run(scratch_url, "--dry-run")
    assert r.returncode == 0 and "dry run: nothing written" in r.stdout, r.stdout + r.stderr
    with psycopg.connect(scratch_url) as c:
        assert c.execute("select count(*) from trades_live").fetchone()[0] == 0

    r = run(scratch_url)
    assert r.returncode == 0 and "committed" in r.stdout, r.stdout + r.stderr
    with psycopg.connect(scratch_url) as c:
        assert c.execute("select count(*) from trades_live").fetchone()[0] == mirror
        assert c.execute("select count(*) from trade_items").fetchone()[0] == transfers
        assert c.execute("select count(*) from trades_live where source = 'import' and season = 2026").fetchone()[0] == mirror
        first, last = c.execute("select min(trade_id), max(trade_id) from trades_live").fetchone()
        assert (first, last) == ("T26-0001", f"T26-{mirror:04d}")
        assert c.execute("select next from trade_sequences where season = 2026").fetchone()[0] == mirror + 1
        # the old id is kept, and every approved trade has one approved_by value (a team abbreviation or ADMIN)
        assert c.execute("select count(*) from trades_live where raw ->> 'legacy_trade_id' is null").fetchone()[0] == 0
        assert c.execute("select count(*) from trades_live where status = 'approved' and approved_by not in "
                         "(select abbr from teams union select 'ADMIN')").fetchone()[0] == 0
    # a second run refuses and writes nothing
    r = run(scratch_url)
    assert r.returncode == 2 and "already has rows" in r.stdout
