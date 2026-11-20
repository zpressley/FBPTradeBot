"""Spec 05 service tests (the 13 numbered tests in specs/05_trade_portal.md, "Tests", plus one for the structural rules).

They need the local Postgres (DATABASE_URL) with migrations applied. The tests commit real rows (the race test needs
separate connections), so World.cleanup() removes everything in `finally`: trades (season 2027, created from
2027-02-01), notifications, throwaway `trade-test-` players with their contracts, roster slots and events, the test
keeper draft, ledger rows (append-only, so cleanup sets fbp.ledger_maintenance = 'on'; local databases only) and the
league_windows rows opened by 'trade-test'. Season 2027 is used because the rules tables are seeded for it.
"""

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import psycopg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from draft import service as ds  # noqa: E402
from league import actions  # noqa: E402
from trades import service as svc  # noqa: E402
from trades.service import NotAdmin, TradeConflict, TradeError  # noqa: E402

BY = "trade-test"
SEASON = 2027
ET = ZoneInfo("America/New_York")
T0 = datetime(2027, 2, 20, 15, 0, tzinfo=timezone.utc)      # inside the KAP trade window the tests open
A, B, C = "CFL", "DRO", "HAM"                                # three non-admin teams
ADMIN = "WIZ"
DRAFT = "trade-test-keeper"


def at(days=0, minutes=0):
    return T0 + timedelta(days=days, minutes=minutes)


@pytest.fixture(scope="session")
def db_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set")
    host = urlparse(url).hostname or ""
    if host not in ("localhost", "127.0.0.1", "::1") and not os.environ.get("FBP_TEST_ALLOW_REMOTE"):
        pytest.skip(f"refusing to run trade tests against non-local database {host!r}")
    return url


class World:
    """Three teams, a 16-round keeper draft, open TRADE and KAP windows, and throwaway players."""

    def __init__(self, url):
        self.url = url
        self.conn = psycopg.connect(url, autocommit=True)
        self.n = 0
        self.cleanup()
        self.start_ledger_id = self.conn.execute("select coalesce(max(id), 0) from wb_ledger").fetchone()[0]
        for w in ("TRADE", "KAP"):
            self.window(w, at(-30), at(30))
        slots = []
        for rnd in range(1, 17):
            for k, team in enumerate((A, B, C), start=1):
                slots.append({"round": rnd, "pick_in_round": k, "original_team": team, "team_abbr": team,
                              "round_type": "vc" if rnd <= 3 else "tc"})
        ds.create_draft(self.conn, DRAFT, SEASON, "keeper", slots, is_test=True)

    # ---- building
    def window(self, name, opens, closes):
        self.conn.execute("""insert into league_windows (season, window_name, opens_at, closes_at, opened_by) values (%s, %s, %s, %s, %s)
                             on conflict (season, window_name) do update set opens_at = excluded.opens_at, closes_at = excluded.closes_at""",
                          (SEASON, name, opens, closes, BY))

    def player(self, team, positions="OF", slot="active", twin=None, tier="TC-1"):
        self.n += 1
        upid = f"trade-test-{self.n}"
        raw = psycopg.types.json.Jsonb({"2wayplayer": twin}) if twin else None
        self.conn.execute("""insert into players (upid, name, positions, mlb_primary_position, player_type, mlb_team, raw)
                             values (%s, %s, %s, %s, 'MLB', 'TST', %s)""", (upid, f"Test Player {self.n}", positions, positions.split(",")[0], raw))
        self.conn.execute("""insert into contracts (upid, team_abbr, kind, tier, acquired_via, valid_from)
                             values (%s, %s, 'keeper', %s, 'import', %s)""", (upid, team, tier, at(-1)))
        self.conn.execute("insert into roster_slots (upid, slot) values (%s, %s)", (upid, slot))
        return upid

    def fund(self, team, amount):
        actions.wb_credit(self.conn, team, amount, "opening_balance", "IN_SEASON", SEASON, created_by=BY)

    def bal(self, team):
        return actions.balance(self.conn, team)

    def overall(self, rnd, team):
        return (rnd - 1) * 3 + (A, B, C).index(team) + 1

    # ---- trade dicts
    @staticmethod
    def P(upid, frm, to):
        return {"kind": "player", "upid": upid, "from_team": frm, "to_team": to}

    @staticmethod
    def W(amount, frm, to):
        return {"kind": "wb", "amount": amount, "from_team": frm, "to_team": to}

    def K(self, rnd, frm, to, owner=None):
        return {"kind": "pick", "draft_id": DRAFT, "overall": self.overall(rnd, owner or frm), "from_team": frm, "to_team": to}

    @staticmethod
    def trade(creator, *items):
        teams = sorted({i["from_team"] for i in items} | {i["to_team"] for i in items})
        return {"season": SEASON, "created_by": creator, "teams": teams, "items": list(items)}

    def accepted(self, creator, *items, now=T0):
        """Send a trade and have every other team accept it. Returns the trade_id."""
        t = self.trade(creator, *items)
        tid = svc.send(self.conn, t, now=now)
        for team in t["teams"]:
            if team != creator:
                svc.accept(self.conn, tid, team, now=now)
        return tid

    # ---- reading
    def owner(self, upid):
        r = self.conn.execute("select team_abbr from contracts where upid = %s and valid_to is null", (upid,)).fetchone()
        return r[0] if r else None

    def status(self, tid):
        return self.conn.execute("select status from trades_live where trade_id = %s", (tid,)).fetchone()[0]

    def notes(self, tid, kind=None):
        sql = "select kind, team_abbr, payload from notifications where payload->>'trade_id' = %s"
        params = [tid]
        if kind:
            sql += " and kind = %s"
            params.append(kind)
        return self.conn.execute(sql + " order by id", params).fetchall()

    def comments(self, tid):
        return self.conn.execute("select kind, team_abbr, body from trade_comments where trade_id = %s order by id", (tid,)).fetchall()

    # ---- cleanup
    def cleanup(self):
        c = self.conn
        ids = [r[0] for r in c.execute("select trade_id from trades_live where season = %s and created_at >= '2027-02-01'", (SEASON,))]
        c.execute("set fbp.trade_maintenance = 'on'")        # items and parties of a non-pending trade are frozen; cleanup only
        c.execute("delete from notifications where payload->>'trade_id' = any(%s)", (ids,))
        c.execute("delete from trade_comments where trade_id = any(%s)", (ids,))
        c.execute("delete from trade_items where trade_id = any(%s)", (ids,))
        with c.transaction():       # trades_live and trade_parties point at each other (the sender FK is deferred)
            c.execute("update trades_live set countered_from = null where trade_id = any(%s)", (ids,))
            c.execute("delete from trade_parties where trade_id = any(%s)", (ids,))
            c.execute("delete from trades_live where trade_id = any(%s)", (ids,))
        c.execute("reset fbp.trade_maintenance")
        if not c.execute("select 1 from trades_live where season = %s", (SEASON,)).fetchone():
            c.execute("delete from trade_sequences where season = %s", (SEASON,))
        c.execute("delete from draft_events where draft_id like 'trade-test-%'")
        c.execute("delete from draft_slots where draft_id like 'trade-test-%'")
        c.execute("delete from drafts where draft_id like 'trade-test-%'")
        c.execute("delete from roster_slots where upid like 'trade-test-%'")
        c.execute("delete from contracts where upid like 'trade-test-%'")
        c.execute("delete from player_events where source_file = 'league_actions' and (upid like 'trade-test-%' or event like '%T27-%')")
        start = getattr(self, "start_ledger_id", None)
        if start is not None:
            c.execute("set fbp.ledger_maintenance = 'on'")   # the ledger is append-only; test cleanup only
            c.execute("delete from wb_ledger where id > %s", (start,))
            c.execute("reset fbp.ledger_maintenance")
        c.execute("delete from roster_assignments where upid like 'trade-test-%'")
        c.execute("delete from position_eligibility where upid like 'trade-test-%'")
        c.execute("delete from players where upid like 'trade-test-%'")
        c.execute("delete from league_windows where opened_by = %s", (BY,))

    def close(self):
        try:
            self.cleanup()
        finally:
            self.conn.close()


@pytest.fixture
def w(db_url):
    world = World(db_url)
    try:
        yield world
    finally:
        world.close()


def blocks(res):
    return {b["code"] for b in res["blocks"]}


def lock_held(url, key):
    """True when another session holds pg_advisory_xact_lock(hashtext(key))."""
    c = psycopg.connect(url, autocommit=True)
    try:
        with c.transaction():
            got = c.execute("select pg_try_advisory_xact_lock(hashtext(%s))", (key,)).fetchone()[0]
        return not got
    finally:
        c.close()


def import_trade(w, tid, legacy, status="approved", by="ADMIN"):
    """A synthetic imported trade (source 'import') in season 2027 so World.cleanup removes it."""
    with w.conn.transaction():      # the sender-is-a-party foreign key is checked at commit
        w.conn.execute("""insert into trades_live (trade_id, season, status, created_by, created_at, expires_at, approved_by, approved_at, source, raw)
                          values (%s, 2027, %s, %s, %s, %s, %s, %s, 'import', %s)""",
                       (tid, status, A, T0, T0 + timedelta(days=7) if status == "pending" else None,
                        by if status == "approved" else None, T0 if status == "approved" else None,
                        psycopg.types.json.Jsonb({"legacy_trade_id": legacy})))
        w.conn.execute("insert into trade_parties (trade_id, team_abbr) values (%s, %s), (%s, %s)", (tid, A, tid, B))


# ---------------------------------------------------------------------------------------------------------------

def test_01_one_for_one_send_to_approved(w):
    p1, p2 = w.player(A, "SP"), w.player(B, "1B")
    tid = svc.send(w.conn, w.trade(A, w.P(p1, A, B), w.P(p2, B, A)), now=T0, note="fair deal?")
    assert tid == "T27-0001" and w.status(tid) == "pending"
    assert [(k, t) for k, t, _ in w.notes(tid)] == [("trade_offer", B)]
    assert svc.accept(w.conn, tid, B, now=at(minutes=2)) == {"trade_id": tid, "status": "accepted"}
    assert w.notes(tid, "trade_admin_card")

    out = svc.approve(w.conn, tid, ADMIN, now=at(minutes=5))
    assert out["status"] == "approved"
    # ownership and contracts travel with the player
    assert (w.owner(p1), w.owner(p2)) == (B, A)
    rows = w.conn.execute("select team_abbr, tier, acquired_via, end_reason from contracts where upid = %s order by id", (p1,)).fetchall()
    assert rows == [(A, "TC-1", "import", "traded"), (B, "TC-1", "trade", None)]
    # player events (type trade) for both players, written by league.actions
    ev = w.conn.execute("select upid, update_type, admin, owner_abbr from player_events where source_file = 'league_actions' "
                        "and upid in (%s, %s) order by upid", (p1, p2)).fetchall()
    assert ev == [(p1, "trade", ADMIN, B), (p2, "trade", ADMIN, A)]
    t = svc.get_trade(w.conn, tid, ADMIN)
    assert (t["status"], t["approved_by"], t["approved_at"]) == ("approved", ADMIN, at(minutes=5))
    # notifications: a DM to each team and the #trades post in the "receives" format with Sub and Proc
    assert sorted(team for _, team, _ in w.notes(tid, "trade_approved")) == [A, B]
    (_, _, post), = w.notes(tid, "trade_post")
    text = post["text"]
    assert post["channel"] == "trades"
    assert f"**{A} receives:**\n1B Test Player 2 [TST] [TC 1]" in text and f"**{B} receives:**\nSP Test Player 1 [TST] [TC 1]" in text
    assert "Sub: SAT 2/20" in text and "Proc: SAT 2/20" in text and f"approved by {ADMIN}" in text
    # a second approve changes nothing
    assert svc.approve(w.conn, tid, ADMIN, now=at(minutes=9))["already_approved"] is True
    assert len(w.notes(tid, "trade_post")) == 1


def test_02_item_moved_by_another_trade_auto_withdraws(w):
    # (a) at accept
    x1, y1, z1 = w.player(A), w.player(B), w.player(C)
    t1 = svc.send(w.conn, w.trade(A, w.P(x1, A, B), w.P(y1, B, A)), now=T0)
    t2 = svc.send(w.conn, w.trade(A, w.P(x1, A, C), w.P(z1, C, A)), now=T0)
    svc.accept(w.conn, t1, B, now=T0)
    svc.approve(w.conn, t1, ADMIN, now=T0)
    with pytest.raises(TradeConflict) as e:
        svc.accept(w.conn, t2, C, now=at(minutes=1))
    assert e.value.status == 409 and "Test Player 1 is on DRO" in str(e.value)
    row = w.conn.execute("select status, close_kind, closed_by from trades_live where trade_id = %s", (t2,)).fetchone()
    assert row == ("withdrawn", "conflict", "system")
    (kind, _, body), = w.comments(t2)
    assert kind == "system" and "ithdrawn automatically" in body and "Test Player 1 is on DRO" in body
    assert w.owner(z1) == C                                    # nothing moved

    # (b) at approve: both accepted first, then the first one is approved
    x2, y2, z2 = w.player(A), w.player(B), w.player(C)
    t3 = w.accepted(A, w.P(x2, A, B), w.P(y2, B, A))
    t4 = w.accepted(A, w.P(x2, A, C), w.P(z2, C, A))
    svc.approve(w.conn, t3, ADMIN, now=T0)
    with pytest.raises(TradeConflict):
        svc.approve(w.conn, t4, ADMIN, now=at(minutes=1))
    assert w.status(t4) == "withdrawn"
    assert w.comments(t4)[0][0] == "system" and w.owner(z2) == C


def test_03_wizbucks_spent_elsewhere_blocks_approval(w):
    p = w.player(B)
    w.fund(A, 20)
    tid = w.accepted(A, w.W(15, A, B), w.P(p, B, A))
    actions.wb_spend(w.conn, A, 10, "auction_win", "IN_SEASON", SEASON, created_by=BY)       # spent elsewhere: $10 left
    before_events = w.conn.execute("select count(*) from player_events where upid = %s", (p,)).fetchone()[0]
    with pytest.raises(TradeError) as e:
        svc.approve(w.conn, tid, ADMIN, now=at(minutes=1))
    assert "CFL has $10" in str(e.value) and "needs $15" in str(e.value)
    assert w.status(tid) == "accepted"
    assert w.owner(p) == B and (w.bal(A), w.bal(B)) == (10, 0)
    assert w.conn.execute("select count(*) from wb_ledger where ref_type = 'trade'").fetchone()[0] == 0
    assert w.conn.execute("select count(*) from player_events where upid = %s", (p,)).fetchone()[0] == before_events


def test_04_r1_pick_needs_both_teams_bought_in(w):
    w.conn.execute("update draft_slots set skip_reason = 'buyin_unpaid' where draft_id = %s and overall = %s",
                   (DRAFT, w.overall(1, B)))                    # DRO is not bought into round 1
    p = w.player(B)
    t = w.trade(A, w.K(1, A, B), w.P(p, B, A))
    res = svc.validate(w.conn, t, "build", T0)
    assert blocks(res) == {"buyin"}
    assert res["blocks"][0]["message"] == "DRO isn't bought into Round 1."
    with pytest.raises(TradeError, match="DRO isn't bought into Round 1"):
        svc.send(w.conn, t, now=T0)
    # the impact panel shows who is bought in
    pick = [x for x in svc.build_preview(w.conn, t, A, T0)["teams"] if x["team"] == A][0]["picks"][0]
    assert pick["bought_in"] == {A: True, B: False} and pick["label"].startswith("R1 ")
    # round 4 needs no buy-in, even with the same teams
    assert svc.validate(w.conn, w.trade(A, w.K(4, A, B), w.P(p, B, A)), "build", T0).ok
    # a pick whose original owner isn't bought in is fine as long as the two TRADING teams are
    assert svc.validate(w.conn, w.trade(A, w.K(1, A, C, owner=A), w.P(w.player(C), C, A)), "build", T0).ok


def test_05_two_way_player_needs_his_other_half(w):
    bat, pit = w.player(A, "DH"), w.player(A, "SP")
    w.conn.execute("update players set raw = jsonb_build_object('2wayplayer', %s::text) where upid = %s", (pit, bat))
    w.conn.execute("update players set raw = jsonb_build_object('2wayplayer', %s::text) where upid = %s", (bat, pit))
    other = w.player(B)
    res = svc.validate(w.conn, w.trade(A, w.P(bat, A, B), w.P(other, B, A)), "build", T0)
    assert blocks(res) == {"two_way"} and "has to be traded with Test Player 2" in res["blocks"][0]["message"]
    with pytest.raises(TradeError):
        svc.send(w.conn, w.trade(A, w.P(bat, A, B), w.P(other, B, A)), now=T0)
    assert svc.validate(w.conn, w.trade(A, w.P(bat, A, B), w.P(pit, A, B), w.P(other, B, A)), "build", T0).ok
    # the other half must go the same way
    assert blocks(svc.validate(w.conn, w.trade(A, w.P(bat, A, B), w.P(pit, A, C)), "build", T0)) == {"two_way"}


def test_06_roster_over_26_warns_but_does_not_block(w):
    have = len(actions._active_players(w.conn, B))
    for _ in range(26 - have):
        w.player(B)                                           # DRO now has exactly 26 active
    p = w.player(A)
    t = w.trade(A, w.P(p, A, B))
    t["items"].append(w.P(w.player(C, slot="farm"), C, A))      # something for CFL to receive
    t["teams"] = sorted({A, B, C})
    # builder / review: a warning, not a block
    res = svc.validate(w.conn, t, "build", T0)
    assert res.ok and any(x["code"] == "roster" and "27 active, max 26" in x["message"] for x in res["warnings"])
    panel = {x["team"]: x for x in svc.build_preview(w.conn, t, A, T0)["teams"]}
    assert panel[B]["roster"]["after"]["active"] == 27 and panel[B]["roster"]["limits"]["active_max"] == 26
    assert any(v["rule"] == "active_max" for v in panel[B]["roster"]["violations"])
    # send, accept, admin queue (warned again) and approve all go through
    tid = svc.send(w.conn, t, now=T0)
    svc.accept(w.conn, tid, B, now=T0)
    svc.accept(w.conn, tid, C, now=T0)
    queue = svc.admin_queue(w.conn, ADMIN, T0)
    mine = [q for q in queue if q["trade"]["trade_id"] == tid][0]
    assert any("27 active" in x["message"] for x in mine["preview"]["warnings"])
    out = svc.approve(w.conn, tid, ADMIN, now=T0)
    assert out["status"] == "approved" and any("27 active" in x["message"] for x in out["warnings"])


def test_07_failure_halfway_changes_nothing(w, monkeypatch):
    p1, p2 = w.player(A), w.player(B)
    w.fund(A, 30)
    tid = w.accepted(A, w.P(p1, A, B), w.W(10, A, B), w.P(p2, B, A))

    def boom(*a, **k):
        raise RuntimeError("forced failure after the players moved")

    monkeypatch.setattr(actions, "wb_credit", boom)
    with pytest.raises(RuntimeError):
        svc.approve(w.conn, tid, ADMIN, now=at(minutes=1))
    monkeypatch.undo()
    assert w.status(tid) == "accepted"
    assert (w.owner(p1), w.owner(p2)) == (A, B)
    assert w.conn.execute("select count(*) from contracts where upid in (%s, %s)", (p1, p2)).fetchone()[0] == 2
    assert w.bal(A) == 30 and w.conn.execute("select count(*) from wb_ledger where ref_type = 'trade'").fetchone()[0] == 0
    assert w.conn.execute("select count(*) from player_events where upid in (%s, %s)", (p1, p2)).fetchone()[0] == 0
    assert not w.notes(tid, "trade_approved") and not w.notes(tid, "trade_post")
    # and the trade can still be approved afterwards
    assert svc.approve(w.conn, tid, ADMIN, now=at(minutes=2))["status"] == "approved"


def test_08_approve_twice_at_the_same_moment_applies_once(w, db_url):
    p1, p2 = w.player(A), w.player(B)
    w.fund(A, 30)
    tid = w.accepted(A, w.P(p1, A, B), w.W(10, A, B), w.P(p2, B, A))
    barrier = threading.Barrier(2)

    def go(_):
        conn = psycopg.connect(db_url, autocommit=True)
        try:
            barrier.wait(timeout=5)
            return svc.approve(conn, tid, ADMIN, now=at(minutes=1))
        finally:
            conn.close()

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(go, range(2)))
    assert sorted(r["already_approved"] for r in results) == [False, True]
    assert (w.owner(p1), w.owner(p2)) == (B, A)
    assert w.conn.execute("select count(*) from contracts where upid = %s and end_reason = 'traded'", (p1,)).fetchone()[0] == 1
    assert w.conn.execute("select count(*) from wb_ledger where ref_type = 'trade' and ref_id = %s", (tid,)).fetchone()[0] == 2
    assert (w.bal(A), w.bal(B)) == (20, 10)
    assert len(w.notes(tid, "trade_post")) == 1


def test_09_non_admin_cannot_approve(w):
    p1, p2 = w.player(A), w.player(B)
    tid = w.accepted(A, w.P(p1, A, B), w.P(p2, B, A))
    for who in (A, B, C):
        with pytest.raises(NotAdmin) as e:
            svc.approve(w.conn, tid, who, now=T0)
        assert e.value.status == 403
    assert w.status(tid) == "accepted" and w.owner(p1) == A
    # the same check guards the other admin paths
    with pytest.raises(NotAdmin):
        svc.void(w.conn, tid, A, "because", now=T0)
    with pytest.raises(NotAdmin):
        svc.admin_manual_entry(w.conn, w.trade(A, w.P(p1, A, B)), A, "note", "tok-9", now=T0)
    with pytest.raises(NotAdmin):
        svc.reject(w.conn, tid, A, "no", now=T0)               # an accepted trade is rejected by an admin only
    assert svc.approve(w.conn, tid, ADMIN, now=T0)["status"] == "approved"      # an admin who is not a party


def test_10_counter_links_and_thread_shows_on_both(w):
    p1, p2, p3 = w.player(A), w.player(B), w.player(B)
    orig = svc.send(w.conn, w.trade(A, w.P(p1, A, B), w.P(p2, B, A)), now=T0, note="first offer")
    svc.comment(w.conn, orig, B, "what about the other guy?", now=at(minutes=1))
    new = svc.counter(w.conn, orig, B, w.trade(B, w.P(p3, B, A), w.P(p1, A, B)), now=at(minutes=2), note="how about this")
    row = w.conn.execute("select status, countered_from, closed_by from trades_live where trade_id = %s", (new,)).fetchone()
    assert row == ("pending", orig, None)
    assert w.conn.execute("select status, closed_by from trades_live where trade_id = %s", (orig,)).fetchone() == ("countered", B)
    assert [x["body"] for x in svc.thread(w.conn, orig, A)] == [x["body"] for x in svc.thread(w.conn, new, A)]
    bodies = [x["body"] for x in svc.thread(w.conn, new, A)]
    assert bodies[0] == "first offer" and "what about the other guy?" in bodies and bodies[-1] == "how about this"
    assert any(b.startswith("Countered by DRO with") for b in bodies)
    assert [(k, t) for k, t, _ in w.notes(new, "trade_countered")] == [("trade_countered", A)]
    # the original can't be answered any more, the counter can
    with pytest.raises(TradeError):
        svc.accept(w.conn, orig, B, now=at(minutes=3))
    with pytest.raises(TradeError):
        svc.counter(w.conn, new, B, w.trade(B, w.P(p3, B, A), w.P(p1, A, B)), now=at(minutes=3))    # the sender can't counter its own offer
    assert svc.accept(w.conn, new, A, now=at(minutes=3))["status"] == "accepted"
    # comment length is capped at 500
    with pytest.raises(TradeError):
        svc.comment(w.conn, new, A, "x" * 501, now=at(minutes=4))
    svc.comment(w.conn, new, A, "x" * 500, now=at(minutes=4))


def test_11_accepted_trade_never_expires_but_pending_does(w):
    p1, p2, p3, p4 = w.player(A), w.player(B), w.player(A), w.player(C)
    acc = w.accepted(A, w.P(p1, A, B), w.P(p2, B, A), now=T0)
    pend = svc.send(w.conn, w.trade(A, w.P(p3, A, C), w.P(p4, C, A)), now=T0)
    assert svc.expire_pending(w.conn, at(days=6)) == []
    assert svc.expire_pending(w.conn, at(days=8)) == [pend]
    assert svc.expire_pending(w.conn, at(days=20)) == []          # safe to run twice
    assert w.status(pend) == "expired"
    assert w.conn.execute("select close_kind, closed_by from trades_live where trade_id = %s", (pend,)).fetchone() == ("expire", "system")
    assert w.status(acc) == "accepted"                             # 20 days with no admin action: still accepted
    assert svc.approve(w.conn, acc, ADMIN, now=at(days=20))["status"] == "approved"
    late = svc.send(w.conn, w.trade(A, w.P(w.player(A), A, B)), now=T0)       # send is fine
    with pytest.raises(TradeError, match="expired"):
        svc.accept(w.conn, late, B, now=at(days=8))                            # accept after 7 days is not


def test_12_deadline_day_is_eleven_fifty_nine_pm_eastern(w):
    p1, p2 = w.player(A), w.player(B)
    deadline = datetime(2027, 7, 31, 23, 59, tzinfo=ET)
    w.window("TRADE", datetime(2027, 3, 1, tzinfo=ET), deadline)
    t = w.trade(A, w.P(p1, A, B), w.P(p2, B, A))
    ok = svc.send(w.conn, t, now=datetime(2027, 7, 31, 23, 58, tzinfo=ET))                  # 11:58 PM ET: goes through
    assert w.status(ok) == "pending"
    for late in (datetime(2027, 8, 1, 0, 1, tzinfo=ET), datetime(2027, 8, 1, 0, 1, tzinfo=ET).astimezone(timezone.utc)):
        res = svc.validate(w.conn, t, "send", late)                                           # 12:01 AM ET: blocked
        assert blocks(res) == {"window"} and "closed" in res["blocks"][0]["message"]
        with pytest.raises(TradeError, match="trade window is closed"):
            svc.send(w.conn, t, now=late)
    # accepting after the deadline is blocked too (the offer stays pending; nothing moves)
    with pytest.raises(TradeError, match="trade window is closed"):
        svc.accept(w.conn, ok, B, now=datetime(2027, 8, 1, 0, 1, tzinfo=ET))
    # the early-morning UTC clock reads Aug 1, but in ET it is still the 31st: 8:30 PM ET is fine (2026 compared UTC dates)
    assert svc.validate(w.conn, t, "send", datetime(2027, 8, 1, 0, 30, tzinfo=timezone.utc)).ok
    # picks need the KAP window as well
    w.window("KAP", at(-30), at(-10))
    assert blocks(svc.validate(w.conn, w.trade(A, w.K(4, A, B), w.P(p2, B, A)), "build", at())) == {"window"}


def test_13_manual_entry_and_void_apply_and_reverse_cleanly(w):
    p1, p2 = w.player(A), w.player(B)
    w.fund(B, 40)
    pick = w.K(5, A, B)
    items = [w.P(p1, A, B), w.P(p2, B, A), pick, w.W(10, B, A)]
    # the note is required
    with pytest.raises(TradeError, match="note"):
        svc.admin_manual_entry(w.conn, w.trade(A, *items), ADMIN, "  ", "tok-13", now=T0)
    out = svc.admin_manual_entry(w.conn, w.trade(A, *items), ADMIN, "agreed on Discord before the window opened", "tok-13", now=T0)
    tid = out["trade_id"]
    t = svc.get_trade(w.conn, tid, ADMIN)
    assert (t["status"], t["source"], t["approved_by"]) == ("approved", "admin_manual", ADMIN)
    assert t["note"].startswith("agreed on Discord")
    assert (w.owner(p1), w.owner(p2)) == (B, A) and (w.bal(A), w.bal(B)) == (10, 30)
    slot = lambda: w.conn.execute("select team_abbr, original_team from draft_slots where draft_id = %s and overall = %s",
                                  (DRAFT, pick["overall"])).fetchone()
    assert slot() == (B, A)                                        # moved, original_team kept
    assert w.notes(tid, "trade_post")

    # a void that cannot finish changes nothing: p1 has been traded on to HAM
    actions.transfer(w.conn, p1, B, C, created_by=BY, season=SEASON, now=at(minutes=1))
    with pytest.raises(actions.RuleError, match="is not on"):
        svc.void(w.conn, tid, ADMIN, "injury", now=at(minutes=2))
    assert w.status(tid) == "approved" and (w.bal(A), w.bal(B)) == (10, 30) and slot() == (B, A) and w.owner(p2) == A
    assert w.conn.execute("select count(*) from wb_ledger where kind = 'reversal'").fetchone()[0] == 0
    actions.transfer(w.conn, p1, C, B, created_by=BY, season=SEASON, now=at(minutes=3))

    # the clean void: everything back, in one transaction, with reversal rows and events
    with pytest.raises(TradeError, match="reason"):
        svc.void(w.conn, tid, ADMIN, " ", now=at(minutes=4))
    assert svc.void(w.conn, tid, ADMIN, "injury void rule", now=at(minutes=5))["status"] == "voided"
    assert (w.owner(p1), w.owner(p2)) == (A, B) and (w.bal(A), w.bal(B)) == (0, 40) and slot() == (A, A)
    kinds = w.conn.execute("select kind, amount from wb_ledger where ref_type = 'reversal' order by amount").fetchall()
    assert kinds == [("reversal", -10), ("reversal", 10)]
    row = w.conn.execute("select status, close_kind, closed_by, close_note, approved_by from trades_live where trade_id = %s", (tid,)).fetchone()
    assert row == ("voided", "void", ADMIN, "injury void rule", ADMIN)
    assert w.conn.execute("select count(*) from player_events where update_type = 'trade_void' and upid in (%s, %s)", (p1, p2)).fetchone()[0] == 2
    assert [k for k, _, _ in w.comments(tid)] == ["system", "system"]
    assert "VOIDED" in w.notes(tid, "trade_post")[-1][2]["text"]
    with pytest.raises(TradeError):
        svc.void(w.conn, tid, ADMIN, "again", now=at(minutes=6))       # only an approved trade can be voided


def test_14_structure_and_cap_rules(w):
    p1, p2, p3 = w.player(A), w.player(B), w.player(C)
    # three teams are fine
    assert svc.validate(w.conn, w.trade(A, w.P(p1, A, B), w.P(p2, B, C), w.P(p3, C, A)), "build", T0).ok
    # one team, the sender not a party, nothing in the trade, a team that gives and gets nothing
    assert "teams" in blocks(svc.validate(w.conn, {"season": SEASON, "created_by": A, "teams": [A], "items": []}, "build", T0))
    t = w.trade(B, w.P(p2, B, C))
    t["created_by"] = A
    assert "teams" in blocks(svc.validate(w.conn, t, "build", T0))
    assert "items" in blocks(svc.validate(w.conn, {"season": SEASON, "created_by": A, "teams": [A, B], "items": []}, "build", T0))
    assert "items" in blocks(svc.validate(w.conn, {**w.trade(A, w.P(p1, A, B)), "teams": [A, B, C]}, "build", T0))
    # duplicates, WizBucks steps, rounds above 15, picks owned by someone else
    assert "items" in blocks(svc.validate(w.conn, w.trade(A, w.P(p1, A, B), w.P(p1, A, B)), "build", T0))
    w.fund(A, 100)
    assert "wb_step" in blocks(svc.validate(w.conn, w.trade(A, w.W(7, A, B), w.P(p2, B, A)), "build", T0))
    assert "pick_round" in blocks(svc.validate(w.conn, w.trade(A, w.K(16, A, B), w.P(p2, B, A)), "build", T0))
    assert "ownership" in blocks(svc.validate(w.conn, w.trade(A, w.K(4, B, A, owner=A), w.P(p1, A, B)), "build", T0))
    # WizBucks already promised in another offer count against the balance
    svc.send(w.conn, w.trade(A, w.W(80, A, B), w.P(p2, B, A)), now=T0)
    res = svc.validate(w.conn, w.trade(A, w.W(30, A, C), w.P(p3, C, A)), "send", T0)
    assert blocks(res) == {"wb_balance"} and "already promised" in res["blocks"][0]["message"]
    panel = {x["team"]: x for x in svc.build_preview(w.conn, w.trade(A, w.W(10, A, C), w.P(p3, C, A)), A, T0)["teams"]}
    assert panel[A]["wb"] == {"now": 100, "after": 90, "out": 10, "in": 0, "tied_up": 80}
    # 12 active offers per team
    for _ in range(11):
        svc.send(w.conn, w.trade(A, w.P(p1, A, B)), now=T0)
    assert blocks(svc.validate(w.conn, w.trade(A, w.P(p1, A, C)), "send", T0)) == {"offer_cap"}
    assert svc.validate(w.conn, w.trade(A, w.P(p1, A, C)), "build", T0).ok is False
    # reject / withdraw rules
    first = w.conn.execute("select trade_id from trades_live order by trade_id limit 1").fetchone()[0]
    with pytest.raises(TradeError, match="reason"):
        svc.reject(w.conn, first, B, " ", now=T0)
    with pytest.raises(TradeError):
        svc.withdraw(w.conn, first, B, now=T0)                       # only the sender withdraws
    assert svc.withdraw(w.conn, first, A, now=T0)["status"] == "withdrawn"
    assert svc.validate(w.conn, w.trade(A, w.P(p1, A, C)), "send", T0).ok       # a slot is free again


# ----------------------------------------------------------------------------------------- review fixes (T1-T15)

def test_16_readers_need_a_viewer_who_is_a_party_or_admin(w):
    p1, p2 = w.player(A), w.player(B)
    tid = svc.send(w.conn, w.trade(A, w.P(p1, A, B), w.P(p2, B, A)), now=T0, note="hi")
    svc.reject(w.conn, tid, B, "no thanks", now=at(minutes=1))                # leaves a system comment
    for who in (A, B, ADMIN):
        assert [x["body"] for x in svc.thread(w.conn, tid, who)][0] == "hi"
        assert svc.get_trade(w.conn, tid, who)["trade_id"] == tid
        assert svc.build_preview(w.conn, svc.get_trade(w.conn, tid, who), who, T0)["teams"]
    with pytest.raises(NotAdmin) as e:
        svc.thread(w.conn, tid, C)                                              # C is not in the trade and not an admin
    assert e.value.status == 403 and isinstance(e.value, svc.NotAParty)
    with pytest.raises(NotAdmin):
        svc.get_trade(w.conn, tid, C)
    with pytest.raises(NotAdmin):
        svc.build_preview(w.conn, svc.get_trade(w.conn, tid, A), C, T0)
    with pytest.raises(NotAdmin):
        svc.build_preview(w.conn, w.trade(A, w.P(p1, A, B)), C, T0)           # a draft: C is not one of its teams
    with pytest.raises(NotAdmin):
        svc.admin_queue(w.conn, A, T0)
    assert svc.admin_queue(w.conn, ADMIN, T0) is not None
    with pytest.raises(TradeError) as e:
        svc.thread(w.conn, "T27-9999", ADMIN)
    assert e.value.status == 404
    # T13: a system comment is stored under the sender's team but the UI-facing thread shows no team
    rows = svc.thread(w.conn, tid, A)
    assert [(r["kind"], r["team_abbr"]) for r in rows] == [("comment", A), ("system", None)]
    assert w.comments(tid)[1][1] == A


def test_17_player_without_a_contract_is_blocked_at_build_and_send(w):
    p = w.player(B)
    w.conn.execute("delete from roster_slots where upid = %s", (p,))
    w.conn.execute("delete from contracts where upid = %s", (p,))
    w.conn.execute("insert into roster_assignments (upid, team_abbr, valid_from, source) values (%s, %s, %s, 'test')", (p, B, at(-1)))
    mine = w.player(A)
    t = w.trade(A, w.P(mine, A, B), w.P(p, B, A))
    res = svc.validate(w.conn, t, "build", T0)
    assert blocks(res) == {"no_contract"} and "no contract row" in res["blocks"][0]["message"]
    with pytest.raises(TradeError, match="no contract row"):
        svc.send(w.conn, t, now=T0)
    # the mirror still words the ownership message: he is on DRO, not on CFL
    assert blocks(svc.validate(w.conn, w.trade(B, w.P(p, A, B), w.P(w.player(B), B, A)), "build", T0)) == {"ownership"}


def test_18_window_closes_at_the_exact_instant(w):
    p1, p2 = w.player(A), w.player(B)
    w.window("TRADE", datetime(2027, 3, 1, tzinfo=ET), datetime(2027, 8, 1, 0, 0, tzinfo=ET))   # meant to allow 11:59 PM on Jul 31
    t = w.trade(A, w.P(p1, A, B), w.P(p2, B, A))
    just_in = datetime(2027, 7, 31, 23, 59, 30, tzinfo=ET)
    assert svc.validate(w.conn, t, "send", just_in).ok
    assert w.status(svc.send(w.conn, t, now=just_in)) == "pending"
    assert blocks(svc.validate(w.conn, t, "send", datetime(2027, 8, 1, 0, 0, tzinfo=ET))) == {"window"}     # closes_at itself is closed


def test_19_manual_entry_double_submit_and_checks(w):
    p1, p2 = w.player(A), w.player(B)
    w.fund(A, 20)
    svc.send(w.conn, w.trade(A, w.W(15, A, B), w.P(p2, B, A)), now=T0)       # 15 of A's 20 is promised elsewhere
    t = w.trade(A, w.P(p1, A, B), w.W(15, A, B))
    with pytest.raises(TradeError, match="client_token"):
        svc.admin_manual_entry(w.conn, t, ADMIN, "by hand", " ", now=T0)
    first = svc.admin_manual_entry(w.conn, t, ADMIN, "by hand", "tok-a", now=T0)     # promised WB is NOT subtracted for an admin entry
    assert first["already_entered"] is False
    again = svc.admin_manual_entry(w.conn, t, ADMIN, "by hand", "tok-a", now=at(minutes=1))
    assert again["trade_id"] == first["trade_id"] and again["already_entered"] is True
    assert w.conn.execute("select count(*) from trades_live where client_token = 'tok-a'").fetchone()[0] == 1
    assert w.conn.execute("select count(*) from wb_ledger where ref_type = 'trade' and ref_id = %s", (first["trade_id"],)).fetchone()[0] == 2
    assert len(w.notes(first["trade_id"], "trade_post")) == 1 and w.owner(p1) == B
    row = w.conn.execute("select note, client_token, source from trades_live where trade_id = %s", (first["trade_id"],)).fetchone()
    assert row == ("by hand", "tok-a", "admin_manual")
    # the structure checks apply: round 16 and a duplicate WB pair are blocked
    with pytest.raises(TradeError, match="Only rounds 1-15"):
        svc.admin_manual_entry(w.conn, w.trade(A, w.K(16, B, A), w.P(w.player(A), A, B)), ADMIN, "by hand", "tok-b", now=T0)
    with pytest.raises(TradeError, match="not an active team|2 or 3"):
        svc.admin_manual_entry(w.conn, {"season": SEASON, "created_by": A, "teams": [A], "items": []}, ADMIN, "by hand", "tok-c", now=T0)
    assert w.conn.execute("select count(*) from trades_live where client_token in ('tok-b', 'tok-c')").fetchone()[0] == 0


def test_20_roster_warnings_use_position_eligibility(w):
    out_p = w.player(B, "OF")            # text says OF ...
    in_p = w.player(C, "OF")
    for upid in (out_p, in_p):           # ... eligibility says SP
        w.conn.execute("insert into position_eligibility (upid, season, position, source) values (%s, %s, 'SP', 'admin')", (upid, SEASON))
    t = w.trade(B, w.P(out_p, B, C), w.P(in_p, C, B))
    panel = {x["team"]: x for x in svc.build_preview(w.conn, t, B, T0)["teams"]}
    roster = panel[B]["roster"]
    # one SP leaves, one SP arrives: the count must not move (it does if eligibility or the season is ignored)
    assert roster["after"]["sp"] == roster["now"]["sp"] and roster["now"]["sp"] >= 1
    assert roster["after"]["pitchers"] == roster["now"]["pitchers"]


def test_21_counter_must_keep_every_original_party_and_ignores_the_original_wb(w):
    p1, p2, p3, pc = w.player(A), w.player(B), w.player(B), w.player(C)
    w.fund(A, 20)
    orig = svc.send(w.conn, w.trade(A, w.W(15, A, B), w.P(p2, B, A)), now=T0)
    with pytest.raises(TradeError, match="every team"):
        svc.counter(w.conn, orig, B, w.trade(B, w.P(p3, B, C), w.P(pc, C, B)), now=at(minutes=1))     # B-C trade: A dropped
    assert w.status(orig) == "pending"
    # the same $15 is asked again in the counter: A's $15 in the original offer is not double counted
    new = svc.counter(w.conn, orig, B, w.trade(B, w.W(15, A, B), w.P(p3, B, A)), now=at(minutes=2))
    assert w.status(orig) == "countered" and w.status(new) == "pending"


def test_22_wizbucks_net_inside_a_trade_and_opposite_pair_blocked(w):
    pa, pb, pc = w.player(A), w.player(B), w.player(C)
    w.fund(A, 30)
    pair = svc.validate(w.conn, w.trade(A, w.W(10, A, B), w.W(5, B, A), w.P(pb, B, A)), "build", T0)
    assert blocks(pair) == {"wb_pair"}
    # a chain A->B->C: B only passes the money on, so it needs none of its own
    chain = w.trade(A, w.W(10, A, B), w.W(10, B, C), w.P(pc, C, A))
    bal = {t: w.bal(t) for t in (A, B, C)}
    assert svc.validate(w.conn, chain, "build", T0).ok            # B's own balance does not matter
    tid = svc.send(w.conn, chain, now=T0)
    svc.accept(w.conn, tid, B, now=T0)
    svc.accept(w.conn, tid, C, now=T0)
    svc.approve(w.conn, tid, ADMIN, now=at(minutes=1))
    assert (w.bal(A), w.bal(B), w.bal(C)) == (bal[A] - 10, bal[B], bal[C] + 10)
    # B sending $10 with nothing coming in is still checked against its balance
    short = svc.validate(w.conn, w.trade(B, w.W(5000, B, A), w.P(pa, A, B)), "build", T0)
    assert blocks(short) == {"wb_balance"}


def test_23_pick_moves_hold_the_draft_lock(w, db_url, monkeypatch):
    p1, p2 = w.player(A), w.player(B)
    pick = w.K(5, A, B)
    tid = w.accepted(A, w.P(p1, A, B), pick, w.P(p2, B, A))
    seen = []
    real = actions.transfer

    def spy(*a, **k):
        seen.append(lock_held(db_url, DRAFT))
        return real(*a, **k)

    monkeypatch.setattr(actions, "transfer", spy)
    assert not lock_held(db_url, DRAFT)
    svc.approve(w.conn, tid, ADMIN, now=at(minutes=1))
    assert seen and all(seen)
    seen.clear()
    svc.void(w.conn, tid, ADMIN, "injury", now=at(minutes=2))
    assert seen and all(seen)
    seen.clear()
    svc.admin_manual_entry(w.conn, w.trade(A, w.P(p1, A, B), w.K(6, A, B), w.P(p2, B, A)), ADMIN, "by hand", "tok-lock", now=at(minutes=3))
    assert seen and all(seen)
    assert not lock_held(db_url, DRAFT)           # released with the transaction


def test_24_skipped_slots_cannot_be_traded_but_still_count_as_bought_in(w):
    pb, pa = w.player(B), w.player(A)
    ov = lambda r, t: w.overall(r, t)
    w.conn.execute("update draft_slots set skip_reason = 'keeper' where draft_id = %s and overall = %s", (DRAFT, ov(2, A)))
    res = svc.validate(w.conn, w.trade(A, w.K(2, A, B), w.P(pb, B, A)), "build", T0)
    assert blocks(res) == {"pick"} and "skipped slot (keeper)" in res["blocks"][0]["message"]
    # A's own round-2 slot is taxed: A is still bought into round 2, so B's round-2 pick can go to A
    w.conn.execute("update draft_slots set skip_reason = 'taxed' where draft_id = %s and overall = %s", (DRAFT, ov(2, A)))
    assert svc.bought_in(w.conn, DRAFT, A, 2)
    assert svc.validate(w.conn, w.trade(B, w.K(2, B, A), w.P(pa, A, B)), "build", T0).ok
    # 'buyin_unpaid' is the only reason that means not bought in
    w.conn.execute("update draft_slots set skip_reason = 'buyin_unpaid' where draft_id = %s and overall = %s", (DRAFT, ov(3, B)))
    assert not svc.bought_in(w.conn, DRAFT, B, 3)
    assert blocks(svc.validate(w.conn, w.trade(A, w.K(3, A, B), w.P(pb, B, A)), "build", T0)) == {"buyin"}


def test_25_database_refuses_inconsistent_trades(w):
    p1, p2 = w.player(A), w.player(B)
    tid = svc.send(w.conn, w.trade(A, w.P(p1, A, B), w.P(p2, B, A)), now=T0)
    # accepted without B's acceptance
    with pytest.raises(psycopg.errors.CheckViolation, match="not every party has accepted"):
        w.conn.execute("update trades_live set status = 'accepted', expires_at = null where trade_id = %s", (tid,))
    assert w.status(tid) == "pending"
    svc.accept(w.conn, tid, B, now=T0)
    svc.approve(w.conn, tid, ADMIN, now=T0)
    # once it is not pending, items and parties are frozen
    with pytest.raises(psycopg.errors.CheckViolation, match="can no longer change"):
        w.conn.execute("insert into trade_items (trade_id, kind, upid, from_team, to_team) values (%s, 'player', %s, %s, %s)", (tid, w.player(A), A, B))
    with pytest.raises(psycopg.errors.CheckViolation, match="can no longer change"):
        w.conn.execute("update trade_parties set accepted_at = null where trade_id = %s", (tid,))
    with pytest.raises(psycopg.errors.CheckViolation, match="can no longer change"):
        w.conn.execute("delete from trade_items where trade_id = %s", (tid,))
    # approved_by is a team or ADMIN
    with pytest.raises(psycopg.errors.CheckViolation, match="neither a team nor ADMIN"):
        w.conn.execute("update trades_live set approved_by = 'zpressley' where trade_id = %s", (tid,))
    w.conn.execute("update trades_live set approved_by = 'ADMIN' where trade_id = %s", (tid,))
    # a manual trade needs a note and a token
    with pytest.raises(psycopg.errors.CheckViolation):
        w.conn.execute("""insert into trades_live (trade_id, season, status, created_by, created_at, expires_at, source, client_token)
                          values ('T27-9100', 2027, 'pending', %s, %s, %s, 'admin_manual', 'x')""", (A, T0, at(days=7)))
    # imports: exempt from the acceptance and frozen-children rules, one row per legacy id
    import_trade(w, "T27-9001", "TRADE-LEGACY-1")
    w.conn.execute("insert into trade_items (trade_id, kind, upid, from_team, to_team) values ('T27-9001', 'player', %s, %s, %s)", (p1, A, B))
    with pytest.raises(psycopg.errors.UniqueViolation):
        import_trade(w, "T27-9002", "TRADE-LEGACY-1")
    # and an import cannot be voided (T2)
    with pytest.raises(TradeError, match="2026 import"):
        svc.void(w.conn, "T27-9001", ADMIN, "injury", now=T0)


def test_26_admin_who_is_a_party_approves_their_own_trade(w):
    p1, p2 = w.player(ADMIN), w.player(B)
    tid = svc.send(w.conn, w.trade(ADMIN, w.P(p1, ADMIN, B), w.P(p2, B, ADMIN)), now=T0)
    svc.accept(w.conn, tid, B, now=T0)
    assert svc.approve(w.conn, tid, ADMIN, now=at(minutes=1))["status"] == "approved"
    t = svc.get_trade(w.conn, tid, ADMIN)
    assert t["approved_by"] == ADMIN and (w.owner(p1), w.owner(p2)) == (B, ADMIN)
    assert f"approved by {ADMIN}" in w.notes(tid, "trade_post")[0][2]["text"]


def test_27_void_blocked_when_the_receiver_spent_the_credit(w):
    p1, p2 = w.player(A), w.player(B)
    w.fund(A, 10)
    tid = w.accepted(A, w.P(p1, A, B), w.W(10, A, B), w.P(p2, B, A))
    svc.approve(w.conn, tid, ADMIN, now=T0)
    actions.wb_spend(w.conn, B, w.bal(B), "auction_win", "IN_SEASON", SEASON, created_by=BY)     # B spends everything, the $10 included
    before = (w.bal(A), w.bal(B))
    with pytest.raises(actions.RuleError):
        svc.void(w.conn, tid, ADMIN, "injury", now=at(minutes=1))
    assert w.status(tid) == "approved" and (w.bal(A), w.bal(B)) == before == (0, 0)
    assert (w.owner(p1), w.owner(p2)) == (B, A)
    assert w.conn.execute("select count(*) from wb_ledger where kind = 'reversal'").fetchone()[0] == 0
    assert not w.notes(tid, "trade_voided")


def test_28_three_team_accept_chain(w):
    p1, p2, p3 = w.player(A), w.player(B), w.player(C)
    tid = svc.send(w.conn, w.trade(A, w.P(p1, A, B), w.P(p2, B, C), w.P(p3, C, A)), now=T0)
    assert [t for _, t, _ in w.notes(tid, "trade_offer")] == [B, C]
    assert svc.accept(w.conn, tid, B, now=at(minutes=1)) == {"trade_id": tid, "status": "pending"}
    assert not w.notes(tid, "trade_admin_card") and w.status(tid) == "pending"
    with pytest.raises(TradeError, match="already accepted"):
        svc.accept(w.conn, tid, B, now=at(minutes=2))
    with pytest.raises(TradeError, match="not part of"):
        svc.accept(w.conn, tid, "WAR", now=at(minutes=2))
    assert svc.accept(w.conn, tid, C, now=at(minutes=3)) == {"trade_id": tid, "status": "accepted"}
    assert len(w.notes(tid, "trade_admin_card")) == 1
    parties = svc.get_trade(w.conn, tid, A)["parties"]
    assert set(parties) == {A, B, C} and all(parties.values())
    svc.approve(w.conn, tid, ADMIN, now=at(minutes=4))
    assert (w.owner(p1), w.owner(p2), w.owner(p3)) == (B, C, A)
