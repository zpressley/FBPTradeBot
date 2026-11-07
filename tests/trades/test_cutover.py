"""scripts/db/cutover_trades.py against a throwaway database (the script commits for real)."""

import os
import subprocess
import sys
import uuid
from urllib.parse import urlparse

import psycopg
import pytest
from psycopg.types.json import Jsonb

from tests.db.conftest import REPO_ROOT, db_url  # noqa: F401
from trades import service as svc
from trades.service import TradeError


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
        with psycopg.connect(scratch, autocommit=True) as c:
            extra_mirror_rows(c)
        yield scratch
    finally:
        admin.execute(f'drop database if exists "{name}" with (force)')
        admin.close()


def extra_mirror_rows(c):
    """Statuses the real 2026 data does not have, so the mapping is tested for every status the script knows.
    They are created last (2026-12), so the ids of the real 95 do not move."""
    rows = [  # legacy id, status, decision_by, acceptances, manager_approved_at, processed_at
        ("X-ADMIN-REJ", "admin_rejected", None, ["CFL"], None, None),
        ("X-APPROVED-ZP", "approved", "zpressley", ["CFL"], None, "2026-12-03T15:00:00Z"),
        ("X-OPEN-PENDING", "pending", None, ["CFL"], None, None),
        ("X-OPEN-REVIEW", "admin_review", None, ["CFL", "DRO"], "2026-12-04T15:00:00Z", None),
    ]
    for n, (legacy, status, by, acc, mgr, proc) in enumerate(rows, start=1):
        c.execute("""insert into trades (trade_id, status, initiator_team, teams, created_at, expires_at, manager_approved_at,
                                         processed_at, admin_decision_by, rejection_reason, rejected_by, raw)
                     values (%s, %s, 'CFL', %s, %s, null, %s, %s, %s, %s, %s, %s)""",
                  (legacy, status, ["CFL", "DRO"], f"2026-12-0{n}T12:00:00Z", mgr, proc, by,
                   "too rich for me" if status == "admin_rejected" else None, "WIZ" if status == "admin_rejected" else None,
                   Jsonb({"acceptances": acc})))
    c.execute("insert into trade_transfers (trade_id, type, upid, from_team, to_team) "
              "select 'X-ADMIN-REJ', 'player', upid, 'CFL', 'DRO' from contracts where team_abbr = 'CFL' and valid_to is null limit 1")
    c.execute("""insert into trade_transfers (trade_id, type, from_team, to_team, raw)
                 values ('X-ADMIN-REJ', 'draft_pick', 'DRO', 'CFL', %s)""",
              (Jsonb({"pick": 4, "type": "draft_pick", "draft": "keeper", "round": 2, "to_team": "CFL", "from_team": "DRO"}),))


def run(url, *args):
    return subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "db" / "cutover_trades.py"), "--database-url", url, *args],
                          cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)


OPEN_LEGACY = ("X-OPEN-PENDING", "X-OPEN-REVIEW")


def test_cutover_trades(scratch_url):
    with psycopg.connect(scratch_url) as c:
        mirror = c.execute("select count(*) from trades").fetchone()[0]
        transfers = c.execute("select count(*) from trade_transfers where trade_id <> all(%s)", (list(OPEN_LEGACY),)).fetchone()[0]
    imported = mirror - len(OPEN_LEGACY)
    r = run(scratch_url, "--dry-run")
    assert r.returncode == 0 and "dry run: nothing written" in r.stdout, r.stdout + r.stderr
    # open work is reported by name, not imported
    for legacy in OPEN_LEGACY:
        assert f"NOT IMPORTED" in r.stdout and legacy in r.stdout
    with psycopg.connect(scratch_url) as c:
        assert c.execute("select count(*) from trades_live").fetchone()[0] == 0

    r = run(scratch_url)
    assert r.returncode == 0 and "committed" in r.stdout, r.stdout + r.stderr
    with psycopg.connect(scratch_url) as c:
        assert c.execute("select count(*) from trades_live").fetchone()[0] == imported
        assert c.execute("select count(*) from trade_items").fetchone()[0] == transfers
        assert c.execute("select count(*) from trades_live where source = 'import' and season = 2026").fetchone()[0] == imported
        first, last = c.execute("select min(trade_id), max(trade_id) from trades_live").fetchone()
        assert (first, last) == ("T26-0001", f"T26-{imported:04d}")
        assert c.execute("select next from trade_sequences where season = 2026").fetchone()[0] == imported + 1
        # the old id is kept (once), and every approved trade has one approved_by value (a team abbreviation or ADMIN)
        assert c.execute("select count(*) from trades_live where raw ->> 'legacy_trade_id' is null").fetchone()[0] == 0
        assert c.execute("select count(*) from trades_live where raw ->> 'legacy_trade_id' = any(%s)", (list(OPEN_LEGACY),)).fetchone()[0] == 0
        assert c.execute("select count(*) from trades_live where status = 'approved' and approved_by not in "
                         "(select abbr from teams union select 'ADMIN')").fetchone()[0] == 0
        # nothing imported is live work
        assert c.execute("select count(*) from trades_live where status in ('pending', 'accepted')").fetchone()[0] == 0
        # the status mapping, per mirror status
        got = dict(c.execute("select m.status, string_agg(distinct l.status, ',') from trades_live l "
                             "join trades m on m.trade_id = l.raw ->> 'legacy_trade_id' group by 1").fetchall())
        assert got == {"approved": "approved", "rejected": "rejected", "admin_rejected": "rejected", "withdrawn": "withdrawn", "expired": "expired"}
        # close fields follow the status
        row = c.execute("select status, close_kind, closed_by, close_note from trades_live where raw ->> 'legacy_trade_id' = 'X-ADMIN-REJ'").fetchone()
        assert row == ("rejected", "reject", "WIZ", "too rich for me")
        assert c.execute("select count(*) from trades_live where status = 'withdrawn' and (close_kind <> 'withdraw' or closed_by <> created_by)").fetchone()[0] == 0
        assert c.execute("select count(*) from trades_live where status = 'expired' and (close_kind <> 'expire' or closed_by <> 'system')").fetchone()[0] == 0
        # "zpressley" becomes ADMIN, and the party missing from raw.acceptances gets an accepted_at (the processing date)
        zp = c.execute("select approved_by, approved_at, trade_id, raw ->> 'admin_decision_by' from trades_live "
                       "where raw ->> 'legacy_trade_id' = 'X-APPROVED-ZP'").fetchone()
        assert zp[0] == "ADMIN" and zp[1].isoformat().startswith("2026-12-03T15:00")
        acc = dict(c.execute("select team_abbr, accepted_at from trade_parties where trade_id = %s", (zp[2],)).fetchall())
        assert acc["CFL"].isoformat().startswith("2026-12-01") or acc["CFL"].isoformat().startswith("2026-12-02")      # the initiator: at created_at
        assert acc["DRO"] == zp[1]
        # every approved trade has every party's accepted_at; a rejected one only has those in raw.acceptances
        assert c.execute("select count(*) from trades_live l join trade_parties p using (trade_id) "
                         "where l.status = 'approved' and p.accepted_at is null").fetchone()[0] == 0
        assert c.execute("select count(*) from trade_parties p join trades_live l using (trade_id) "
                         "where l.raw ->> 'legacy_trade_id' = 'X-ADMIN-REJ' and p.accepted_at is not null").fetchone()[0] == 1
        # pick items: no slot, the 2026 transfer is in raw
        picks = c.execute("select draft_id, overall, raw from trade_items where kind = 'pick'").fetchall()
        assert len(picks) == c.execute("select count(*) from trade_transfers where type = 'draft_pick' and trade_id <> all(%s)",
                                       (list(OPEN_LEGACY),)).fetchone()[0] >= 15
        assert all(d is None and o is None and r["type"] == "draft_pick" and {"round", "pick", "draft"} <= set(r) for d, o, r in picks)

        # T1: receives() works on every imported trade, and its pick lines come from raw
        lines = 0
        for (tid,) in c.execute("select trade_id from trades_live order by trade_id").fetchall():
            out = svc.receives(c, tid)
            assert out and all(isinstance(v, list) for v in out.values())
            lines += sum(len(v) for v in out.values())
        assert lines == imported_items(c)
        rec = svc.receives(c, c.execute("select trade_id from trades_live where raw ->> 'legacy_trade_id' = 'X-ADMIN-REJ'").fetchone()[0])
        assert "Keeper Pick R2 P4 via DRO" in rec["CFL"]
        # a minimal preview for an import (no pick or buy-in panel), and the readers' viewer check
        tid = zp[2]
        prev = svc.build_preview(c, svc.get_trade(c, tid, "WIZ"), "WIZ", None)
        assert prev["source"] == "import" and prev["blocks"] == [] and all(set(x) == {"team"} for x in prev["teams"])
        with pytest.raises(svc.NotAdmin):
            svc.get_trade(c, tid, "HAM")
        assert svc.thread(c, tid, "CFL") == []
        # T2: an import cannot be voided or approved
        with pytest.raises(TradeError, match="2026 import"):
            svc.void(c, tid, "WIZ", "injury")
        # the database exempts imports from the "every party accepted" and "frozen children" rules
        c.execute("update trade_parties set accepted_at = null where trade_id = %s", (tid,))
        c.commit()
    # a second run refuses and writes nothing
    r = run(scratch_url)
    assert r.returncode == 2 and "already has rows" in r.stdout


def imported_items(c):
    return c.execute("select count(*) from trade_items").fetchone()[0]
