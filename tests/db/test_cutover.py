"""scripts/db/cutover_contracts.py against a throwaway database (the script commits for real, and the
ledger is append-only, so the shared test database is never used)."""

import json
import os
import subprocess
import sys
import uuid
from urllib.parse import urlparse

import psycopg
import pytest

from scripts.db import cutover_contracts as cut
from tests.db.conftest import REPO_ROOT


@pytest.fixture(scope="module")
def scratch_url(db_url):
    name = "fbp_cutover_" + uuid.uuid4().hex[:8]
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


def run(scratch_url, *args, repo_root=None):
    argv = [sys.executable, str(REPO_ROOT / "scripts" / "db" / "cutover_contracts.py"), "--database-url", scratch_url, *args]
    if repo_root:
        argv += ["--repo-root", str(repo_root)]
    return subprocess.run(argv, cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)


def counts(url):
    with psycopg.connect(url) as c:
        return c.execute("select (select count(*) from contracts), (select count(*) from roster_slots), "
                         "(select count(*) from wb_ledger)").fetchone()


def test_cutover(scratch_url, tmp_path):
    # 1. no resolutions: refuses, writes nothing, shows a template and the confirm string
    r = run(scratch_url, "--opening-balances")
    assert r.returncode == 2 and "without a resolution" in r.stdout
    assert '--confirm-balances "' in r.stdout
    assert "--allow-conflicts" not in r.stdout and counts(scratch_url) == (0, 0, 0)

    # 2. Zach's file: one entry per open conflict
    with psycopg.connect(scratch_url) as c:
        _, _, conflicts, _, _ = cut.build_contracts(c)
    assert conflicts
    res = {}
    for k in conflicts:
        res[k["upid"]] = ({"kind": "keeper", "tier": "TC-1", "team": "WIZ"} if k["owned"] else {"tier": None})
    # an entry for a player with no conflict is refused
    with psycopg.connect(scratch_url) as c:
        clean = c.execute("select upid from roster_assignments where valid_to is null and contract_type = 'keeper' "
                          "and upid <> all(%s) limit 1", (list(res),)).fetchone()[0]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({**res, clean: {"kind": "keeper", "tier": "TC-1"}}))
    r = run(scratch_url, "--resolutions", str(bad))
    assert r.returncode == 2 and "has no conflict" in r.stdout and counts(scratch_url) == (0, 0, 0)
    # one conflict left unanswered: refused
    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps(dict(list(res.items())[1:])))
    assert run(scratch_url, "--resolutions", str(partial)).returncode == 2 and counts(scratch_url) == (0, 0, 0)
    # a bad resolution is refused
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({**res, next(iter(res)): {"kind": "keeper", "tier": "P"}}))
    assert run(scratch_url, "--resolutions", str(invalid)).returncode == 2 and counts(scratch_url) == (0, 0, 0)

    good = tmp_path / "good.json"
    good.write_text(json.dumps(res))
    # 3. balances need the confirm string; a wrong one writes nothing, not even the contracts
    assert run(scratch_url, "--resolutions", str(good), "--opening-balances").returncode == 2
    assert run(scratch_url, "--resolutions", str(good), "--opening-balances", "--confirm-balances", "1,2,3").returncode == 2
    assert counts(scratch_url) == (0, 0, 0)
    dry = run(scratch_url, "--resolutions", str(good), "--opening-balances", "--dry-run")
    assert dry.returncode == 0 and counts(scratch_url) == (0, 0, 0)
    confirm = next(l for l in dry.stdout.splitlines() if l.startswith("--confirm-balances")).split('"')[1]
    assert len(confirm.split(",")) == 12 and "IL and NA slots are NOT set" in dry.stdout

    # 4. a balances file with a missing team stops the whole thing (contracts included)
    short = tmp_path / "data"
    short.mkdir()
    wiz = json.loads((REPO_ROOT / "data" / "wizbucks.json").read_text())
    wiz.pop(next(iter(wiz)))
    (short / "wizbucks.json").write_text(json.dumps(wiz))
    r = run(scratch_url, "--resolutions", str(good), "--opening-balances", "--confirm-balances", confirm, repo_root=tmp_path)
    assert r.returncode == 2 and "not in wizbucks.json" in r.stdout and counts(scratch_url) == (0, 0, 0)

    # 5. the real run: one transaction, contracts + slots + balances
    r = run(scratch_url, "--resolutions", str(good), "--opening-balances", "--confirm-balances", confirm)
    assert r.returncode == 0, r.stdout + r.stderr
    with psycopg.connect(scratch_url) as c:
        n_contracts, n_slots, n_ledger = counts(scratch_url)
        assert n_contracts == n_slots > 0 and n_ledger == 12
        assert c.execute("""select count(*) from contracts k join roster_slots s using (upid)
                             where (k.kind = 'keeper' and s.slot <> 'active') or (k.kind = 'prospect' and s.slot <> 'farm')""").fetchone()[0] == 0
        assert c.execute("select string_agg(balance::text, ',' order by team_abbr) from wb_balances").fetchone()[0] == confirm
        assert c.execute("select count(*) from contracts where upid = %s and tier = 'TC-1' and team_abbr = 'WIZ'",
                         (next(iter(k for k in res if res[k].get("kind"))),)).fetchone()[0] == 1
    # 6. a second run does nothing
    assert run(scratch_url, "--resolutions", str(good), "--opening-balances", "--confirm-balances", confirm).returncode == 1
    assert counts(scratch_url) == (n_contracts, n_slots, 12)
