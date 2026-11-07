"""Spec 02 service tests 1-7. They need the local Postgres (DATABASE_URL) with migrations applied.

The tests commit real rows (concurrency needs separate connections), so every test cleans up in
`finally`: bids, decisions, results, weeks, ledger rows, contracts, slots, events and the
throwaway players 'auction-test-*'. The shadow mirror must stay clean for reconcile.py.
"""

import os
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import psycopg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from auction import rules, service  # noqa: E402
from auction.rules import Bid, BidError  # noqa: E402

ET = ZoneInfo("America/New_York")
CAL = {"start": "2027-03-08", "all_star_break_start": "2027-07-12",
       "restart": "2027-07-19", "playoffs_start": "2027-11-29", "season": 2027}
MON = datetime(2027, 4, 5, tzinfo=ET)          # auction week Monday (no DST change inside it)
WEEK = MON.date()
BY = "auction-test"


def at(day, h, m=0):
    return (MON + timedelta(days=day)).replace(hour=h, minute=m)


MON_4PM, WED_NOON, THU_NOON, SUN_NOON = at(0, 16), at(2, 12), at(3, 12), at(6, 12)


@pytest.fixture(scope="session")
def db_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set")
    host = urlparse(url).hostname or ""
    if host not in ("localhost", "127.0.0.1", "::1") and not os.environ.get("FBP_TEST_ALLOW_REMOTE"):
        pytest.skip(f"refusing to run auction tests against non-local database {host!r}")
    return url


def connect(url):
    return psycopg.connect(url, autocommit=True)


class World:
    """Throwaway prospects and funded teams, removed again by close()."""

    def __init__(self, url, n_prospects=3):
        self.url = url
        self.conn = connect(url)
        self.active = [r[0] for r in self.conn.execute("select abbr from teams where active order by abbr")]
        assert len(self.active) >= 5, "need at least 5 active teams"
        self.teams = self.active[:5]
        self.upids = [f"auction-test-{i}" for i in range(1, n_prospects + 1)]
        self.cleanup()  # leftovers from a killed run
        for u in self.upids:
            self.conn.execute("insert into players (upid, name, player_type) values (%s, %s, 'Farm')", (u, f"Test {u}"))

    def order(self, first=()):
        """Priority order: `first` (worst standings) then the other active teams."""
        return list(first) + [t for t in self.active if t not in first]

    def source(self, first=()):
        return lambda: self.order(first)

    def fund(self, team, amount):
        self.conn.execute("select wb_credit(%s, %s, 'opening_balance', 'IN_SEASON', 2027, null, null, null, 'auction test', %s)",
                          (team, amount, BY))

    def debit(self, team, amount):
        self.conn.execute("select wb_spend(%s, %s, 'admin_adjustment', 'IN_SEASON', 2027, null, null, null, 'auction test', %s)",
                          (team, amount, BY))

    def count(self, sql, params=None):
        return self.conn.execute(sql, params).fetchone()[0]

    def cleanup(self):
        c = self.conn
        like = "auction-test-%"
        c.execute("delete from auction_results where upid like %s", (like,))
        c.execute("delete from auction_decisions where upid like %s", (like,))
        c.execute("delete from auction_bids where upid like %s", (like,))
        c.execute("delete from auction_weeks w where not exists (select 1 from auction_bids b where b.week_start = w.week_start)"
                  " and week_start = %s", (WEEK,))
        c.execute("delete from player_events where upid like %s", (like,))
        c.execute("delete from roster_slots where upid like %s", (like,))
        c.execute("delete from contracts where upid like %s", (like,))
        c.execute("delete from roster_assignments where upid like %s", (like,))
        c.execute("set fbp.ledger_maintenance = 'on'")   # the ledger is append-only; test cleanup only
        c.execute("delete from wb_ledger where created_by = %s or upid like %s", (BY, like))
        c.execute("reset fbp.ledger_maintenance")
        c.execute("delete from players where upid like %s", (like,))

    def close(self):
        try:
            self.cleanup()
        finally:
            self.conn.close()


@pytest.fixture
def world(db_url):
    w = World(db_url)
    try:
        yield w
    finally:
        w.close()


def run_threads(url, jobs, workers=10):
    """Run fn(conn) for each job on its own connection. Returns [(ok, value_or_exception)]."""
    barrier = threading.Barrier(min(workers, len(jobs)))

    def one(job):
        conn = connect(url)
        try:
            try:
                barrier.wait(timeout=5)
            except threading.BrokenBarrierError:
                pass
            try:
                return True, job(conn)
            except Exception as e:  # noqa: BLE001 - the tests inspect the type
                return False, e
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(one, jobs))


# --------------------------------------------------------------------------- 1

def test_01_concurrent_bids_respect_balance_and_raise_rules(world):
    t1, t2, t3, t4 = world.teams[:4]
    p1, p2, p3 = world.upids
    order = world.order([t1, t2, t3, t4])
    for t in (t1, t2, t3, t4):
        world.fund(t, 100)
    for t, p in ((t1, p1), (t2, p2), (t3, p3)):
        service.place_bid(world.conn, t, p, "OB", 10, "web", MON_4PM, CAL, world.source([t1, t2, t3, t4]))

    rng = random.Random(7)
    jobs = []
    for _ in range(50):
        team, upid = rng.choice([t1, t2, t3, t4]), rng.choice([p1, p2, p3])
        amount, when = rng.choice(range(15, 71, 5)), rng.choice([WED_NOON, WED_NOON, THU_NOON, at(4, 12)])
        jobs.append(lambda c, a=(team, upid, "CB", amount, "web", when): service.place_bid(c, *a, CAL))
    results = run_threads(world.url, jobs)

    for ok, v in results:
        assert ok or isinstance(v, BidError), v          # only rule rejections, never a crash
    accepted = sum(ok for ok, _ in results)
    assert accepted >= 4                                 # the run really exercised accepted bids

    # Replay the accepted bids in the order they committed (the lock makes seq that order).
    rows = world.conn.execute("""select team_abbr, upid, bid_type, amount, placed_at from auction_bids
                                  where week_start = %s order by seq""", (WEEK,)).fetchall()
    assert len(rows) == 3 + accepted
    seen = []
    for team, upid, bt, amount, placed in rows:
        if bt == "CB":
            hb = rules.high_bid(upid, seen, order)
            assert amount >= hb[1] + 5 or (amount == hb[1] and order.index(team) < order.index(hb[0])), \
                f"{team} bid {amount} over {hb}"
            assert team != next(b.team for b in seen if b.upid == upid and b.bid_type == "OB")
        seen.append(Bid(team, upid, bt, amount, placed))
        for t in (t1, t2, t3, t4):
            assert rules.committed(t, seen, order) <= 100, f"{t} over-committed after {team} {amount}"


# --------------------------------------------------------------------------- 2

def test_02_two_concurrent_obs_on_one_prospect_one_wins(world):
    a, b = world.teams[:2]
    world.fund(a, 50)
    world.fund(b, 50)
    src = world.source()
    jobs = [lambda c, t=t: service.place_bid(c, t, world.upids[0], "OB", 10, "web", MON_4PM, CAL, src) for t in (a, b)]
    results = run_threads(world.url, jobs, workers=2)
    assert sorted(ok for ok, _ in results) == [False, True]
    err = next(v for ok, v in results if not ok)
    assert isinstance(err, BidError) and "already has an originating bid" in str(err)
    assert world.count("select count(*) from auction_bids where upid = %s", (world.upids[0],)) == 1


# --------------------------------------------------------------------------- 3

def test_03_resolve_twice_at_once_resolves_once(world):
    a, b = world.teams[:2]
    world.fund(a, 100)
    world.fund(b, 100)
    src = world.source()
    service.place_bid(world.conn, a, world.upids[0], "OB", 10, "web", MON_4PM, CAL, src)
    service.place_bid(world.conn, b, world.upids[0 + 1], "OB", 15, "web", MON_4PM, CAL, src)
    service.place_bid(world.conn, b, world.upids[0], "CB", 20, "web", WED_NOON, CAL)

    results = run_threads(world.url, [lambda c: service.resolve_week(c, WEEK, "scheduler", SUN_NOON)] * 2, workers=2)
    assert all(ok for ok, _ in results), results
    assert sorted(v["already_resolved"] for _, v in results) == [False, True]
    assert results[0][1]["summary"] == results[1][1]["summary"]
    assert world.count("select count(*) from auction_results where week_start = %s", (WEEK,)) == 2
    debits = world.count("select count(*) from wb_ledger where ref_type = 'auction' and upid like 'auction-test-%'")
    assert debits == 2
    assert world.count("select count(*) from contracts where upid like 'auction-test-%' and valid_to is null") == 2
    bal = dict(world.conn.execute("select team_abbr, balance from wb_balances where team_abbr = any(%s)", ([a, b],)).fetchall())
    assert bal == {a: 100, b: 100 - 20 - 15}             # b wins both: 20 CB win + 15 OB-only


# --------------------------------------------------------------------------- 4

def test_04_exception_after_ledger_insert_rolls_everything_back(world, monkeypatch):
    a, b = world.teams[:2]
    world.fund(a, 100)
    world.fund(b, 100)
    src = world.source()
    service.place_bid(world.conn, a, world.upids[0], "OB", 10, "web", MON_4PM, CAL, src)
    service.place_bid(world.conn, b, world.upids[1], "OB", 12, "web", MON_4PM, CAL, src)

    def boom(*args, **kwargs):
        raise RuntimeError("killed mid-resolution")

    monkeypatch.setattr(service, "_event", boom)         # runs right after the first ledger debit
    with pytest.raises(RuntimeError):
        service.resolve_week(world.conn, WEEK, "scheduler", SUN_NOON)
    monkeypatch.undo()

    assert world.count("select count(*) from auction_results where week_start = %s", (WEEK,)) == 0
    assert world.count("select count(*) from wb_ledger where ref_type = 'auction'") == 0
    assert world.count("select count(*) from contracts where upid like 'auction-test-%'") == 0
    assert world.count("select count(*) from player_events where upid like 'auction-test-%'") == 0
    status, resolved_at = world.conn.execute("select status, resolved_at from auction_weeks where week_start = %s", (WEEK,)).fetchone()
    assert (status, resolved_at) == ("open", None)

    out = service.resolve_week(world.conn, WEEK, "scheduler", SUN_NOON)   # a retry works
    assert out["already_resolved"] is False and len(out["summary"]["results"]) == 2


# --------------------------------------------------------------------------- 5

def test_05_ob_only_pays_its_own_amount(world):
    a, b = world.teams[:2]
    world.fund(a, 100)
    world.fund(b, 100)
    src = world.source()
    service.place_bid(world.conn, a, world.upids[0], "OB", 25, "web", MON_4PM, CAL, src)
    service.place_bid(world.conn, b, world.upids[1], "OB", 10, "web", MON_4PM, CAL, src)
    out = service.resolve_week(world.conn, WEEK, "scheduler", SUN_NOON)
    got = {r["upid"]: (r["team"], r["amount"], r["outcome"]) for r in out["summary"]["results"]}
    assert got == {world.upids[0]: (a, 25, "OB_ONLY"), world.upids[1]: (b, 10, "OB_ONLY")}
    paid = dict(world.conn.execute("select team_abbr, -amount from wb_ledger where kind = 'auction_win' and upid like 'auction-test-%'").fetchall())
    assert paid == {a: 25, b: 10}
    row = world.conn.execute("select team_abbr, prospect_type, acquired_via from contracts where upid = %s and valid_to is null",
                             (world.upids[0],)).fetchone()
    assert row == (a, "PC", "auction")


# --------------------------------------------------------------------------- 6

def test_06_affordability_cascades_to_the_next_team(world):
    a, b, c, d, e = world.teams
    p1, p2, p3 = world.upids
    first = [a, b, c, d, e]
    for t, amt in ((a, 60), (b, 45), (c, 50), (d, 50), (e, 50)):
        world.fund(t, amt)
    src = world.source(first)
    for t, p in ((c, p1), (d, p2), (e, p3)):
        service.place_bid(world.conn, t, p, "OB", 10, "web", MON_4PM, CAL, src)
    for p in (p1, p2, p3):                               # B opens at 15 each (45 committed)
        service.place_bid(world.conn, b, p, "CB", 15, "web", WED_NOON, CAL)
    for i, p in enumerate((p1, p2, p3)):                 # A tops each at 20, p3 last (60 committed)
        service.place_bid(world.conn, a, p, "CB", 20, "web", THU_NOON + timedelta(minutes=i), CAL)
    world.debit(a, 20)                                   # A can now only afford two wins
    world.debit(b, 35)                                   # B (10 left) can't afford the win it would inherit

    out = service.resolve_week(world.conn, WEEK, "scheduler", SUN_NOON)
    got = {r["upid"]: (r["team"], r["amount"], r["outcome"], r["dropped"]) for r in out["summary"]["results"]}
    assert got == {p1: (a, 20, "CB_WIN", []), p2: (a, 20, "CB_WIN", []),
                   p3: (e, 10, "OB_ONLY", [a, b])}
    bal = dict(world.conn.execute("select team_abbr, balance from wb_balances where team_abbr = any(%s)", (world.teams,)).fetchall())
    assert bal == {a: 0, b: 10, c: 50, d: 50, e: 40}


# --------------------------------------------------------------------------- 7

AUCTION_TABLES = ("auction_weeks", "auction_bids", "auction_decisions", "auction_results",
                  "wb_ledger", "contracts", "player_events", "players")


def _writes(conn):
    conn.execute("select pg_stat_force_next_flush()")
    stats = conn.execute("""select relname, n_tup_ins, n_tup_upd, n_tup_del from pg_stat_user_tables
                             where relname = any(%s) order by relname""", (list(AUCTION_TABLES),)).fetchall()
    counts = [conn.execute(f"select count(*) from {t}").fetchone()[0] for t in AUCTION_TABLES]
    return stats, counts


def test_07_get_board_100_times_writes_nothing(world):
    a, b = world.teams[:2]
    world.fund(a, 100)
    world.fund(b, 100)
    reader = connect(world.url)
    try:
        before = _writes(reader)
        for i in range(50):                              # no week row yet: still must not create one
            board = service.get_board(reader, MON_4PM, CAL, team=a, priority_source=world.source())
        assert board["week_status"] is None and board["phase"] == rules.OB_WINDOW
        assert _writes(reader) == before

        src = world.source()
        service.place_bid(world.conn, a, world.upids[0], "OB", 10, "web", MON_4PM, CAL, src)
        service.place_bid(world.conn, b, world.upids[0], "CB", 15, "web", WED_NOON, CAL)
        before = _writes(reader)
        for i in range(100):
            board = service.get_board(reader, WED_NOON + timedelta(minutes=i), CAL, team=b if i % 2 else None)
        after = _writes(reader)
        assert after == before
        row = board["prospects"][0]
        assert (row["ob_team"], row["high_team"], row["high_amount"], row["cb_count"]) == (a, b, 15, 1)
        assert [x["amount"] for x in row["bids"]] == [10, 15]
        assert board["me"]["committed"] == 15 and board["me"]["available"] == 85
    finally:
        reader.close()
