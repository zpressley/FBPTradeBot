"""Spec 04 service tests (the ones that need no Discord and no browser). They need the local Postgres
(DATABASE_URL) with migrations applied and the JSON import loaded (real players are used).

The tests commit real rows (the race tests need separate connections), so every test cleans up in
`finally`: notifications, events, picks, slots, contracts, prepaid slots, ledger rows (the ledger is
append-only, so cleanup sets fbp.ledger_maintenance = 'on'), boards, throwaway players and drafts.
All test drafts have ids starting 'dt-'. Test drafts use season 2027 (the season the rules tables are
seeded for) and the speed-award tests use season 2098.
"""

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from draft import service as ds  # noqa: E402
from draft.service import DraftError  # noqa: E402
from league import actions  # noqa: E402

PICKS = ds.PICKS_TABLE
BY = "draft-test"
SEASON = 2027
AWARD_SEASON = 2098
T0 = datetime(2027, 2, 14, 15, 0, tzinfo=timezone.utc)
NEVER = 10 ** 9      # clock_tick stale_seconds: "the runner was never down" when a test jumps the clock


def at(seconds):
    return T0 + timedelta(seconds=seconds)


@pytest.fixture(scope="session")
def db_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set")
    host = urlparse(url).hostname or ""
    if host not in ("localhost", "127.0.0.1", "::1") and not os.environ.get("FBP_TEST_ALLOW_REMOTE"):
        pytest.skip(f"refusing to run draft tests against non-local database {host!r}")
    return url


def connect(url):
    return psycopg.connect(url, autocommit=True)


class World:
    """Throwaway drafts on real imported players, removed again by close()."""

    def __init__(self, url):
        self.url = url
        self.conn = connect(url)
        self.teams = [r[0] for r in self.conn.execute("select abbr from teams where active order by abbr")]
        assert len(self.teams) >= 8
        self.used = set()      # upids the test touched (contracts, events, slots are removed for these)
        self.boards = set()    # (season, draft_type, team, slot_key)
        self.cleanup()         # leftovers from a killed run

    # ---- building
    def slots(self, draft_type, rounds):
        """rounds = [[team, team, ...], ...] (round numbers 1..n) or [(round_no, [teams]), ...]."""
        if rounds and not isinstance(rounds[0], tuple):
            rounds = list(enumerate(rounds, start=1))
        out = []
        for rno, teams in rounds:
            for i, t in enumerate(teams, start=1):
                rt = ("fypd" if rno <= 2 else "open") if draft_type == "prospect" else ("vc" if rno <= 3 else "tc")
                out.append({"round": rno, "pick_in_round": i, "original_team": t, "team_abbr": t, "round_type": rt})
        return out

    def draft(self, name, rounds, draft_type="keeper", clock=180, is_test=True, season=SEASON, start=True, check=False, at_s=0):
        did = f"dt-{name}"
        ds.create_draft(self.conn, did, season, draft_type, self.slots(draft_type, rounds), clock_seconds=clock, is_test=is_test)
        if start:
            ds.start(self.conn, did, "tester", at(at_s), check=check)
        return did

    def players(self, did, n, round=None, offset=0, fypd=None):
        """n eligible upids for the draft (and round), tracked for cleanup."""
        rows = self.conn.execute("select upid from draft_pool(%s, %s) where eligible and (%s::boolean is null or fypd is not distinct from %s) "
                                 "order by upid::bigint limit %s offset %s", (did, round, fypd, fypd, n, offset)).fetchall()
        ups = [r[0] for r in rows]
        assert len(ups) == n
        self.used.update(ups)
        return ups

    def owned(self):
        """A player the mirror says is owned (ineligible for any draft)."""
        r = self.conn.execute("select p.upid from players p join roster_assignments a on a.upid = p.upid and a.valid_to is null "
                              "where p.player_type = 'MLB' order by p.upid::bigint limit 1").fetchone()
        return r[0]

    def board(self, did, team, slot, upids):
        d = self.conn.execute("select season, draft_type from drafts where draft_id = %s", (did,)).fetchone()
        ds.set_board(self.conn, did, team, slot, upids)
        self.boards.add((d[0], d[1], team, "any" if slot == "any" else f"{did}:{slot}"))
        self.used.update(upids)

    def prepay(self, team, slot_type, n=1):
        """Buy n prepaid prospect slots (a funded team, an open PAD window)."""
        c = self.conn
        actions.open_window(c, SEASON, "PAD", T0 - timedelta(days=1), T0 + timedelta(days=60), BY)
        actions.wb_credit(c, team, 200, "opening_balance", "PAD", SEASON, created_by=BY)
        actions.buy_draft_slot(c, team, slot_type, created_by=BY, season=SEASON, count=n, now=T0)

    def tick(self, did, seconds, stale=NEVER):
        return ds.clock_tick(self.conn, did, at(seconds), stale_seconds=stale)

    def notes(self, kind=None, after=0, team=None):
        sql = "select id, kind, team_abbr, payload from notifications where id > %s and payload->>'draft_id' like 'dt-%%'"
        params = [after]
        if kind:
            sql += " and kind = %s"
            params.append(kind)
        if team:
            sql += " and team_abbr = %s"
            params.append(team)
        return [{"id": r[0], "kind": r[1], "team": r[2], **r[3]} for r in self.conn.execute(sql + " order by id", params).fetchall()]

    def last_note(self):
        return self.conn.execute("select coalesce(max(id), 0) from notifications").fetchone()[0]

    def clock(self, did):
        return self.conn.execute("select on_clock_overall, deadline_at, paused_remaining_ms, warned from draft_clock where draft_id = %s",
                                 (did,)).fetchone()

    def picks(self, did):
        return self.conn.execute(f"select overall, upid, team_abbr, source, seconds_used, contract_tier from {PICKS} "
                                 "where draft_id = %s order by overall", (did,)).fetchall()

    def status(self, did):
        return self.conn.execute("select status from drafts where draft_id = %s", (did,)).fetchone()[0]

    def contract(self, upid):
        return self.conn.execute("select team_abbr, kind, tier, prospect_type, acquired_via, acquired_round from contracts "
                                 "where upid = %s and valid_to is null", (upid,)).fetchone()

    # ---- cleanup
    def cleanup(self):
        c = self.conn
        like = "dt-%"
        newcomers = [r[0] for r in c.execute("select upid from players where mlb_id between 9990000 and 9999999")]
        upids = set(self.used) | set(newcomers) | {r[0] for r in c.execute(f"select upid from {PICKS} where draft_id like %s", (like,))}
        c.execute("delete from notifications where payload->>'draft_id' like %s or payload->>'draft_id' like 'season-%%'", (like,))
        c.execute("delete from draft_events where draft_id like %s", (like,))
        c.execute("delete from draft_add_requests where draft_id like %s", (like,))
        c.execute(f"delete from {PICKS} where draft_id like %s", (like,))
        c.execute("delete from draft_clock where draft_id like %s", (like,))
        c.execute("delete from draft_forklift where draft_id like %s", (like,))
        c.execute("delete from draft_rankings where draft_id like %s", (like,))
        c.execute("delete from draft_pool_overrides where draft_id like %s", (like,))
        c.execute("delete from draft_slots where draft_id like %s", (like,))
        c.execute("delete from drafts where draft_id like %s", (like,))
        for season, dtype, team, key in self.boards:
            c.execute("delete from draft_boards where season = %s and draft_type = %s and team_abbr = %s and slot_key = %s",
                      (season, dtype, team, key))
        c.execute("delete from draft_boards where slot_key like %s", (like,))
        if upids:
            ups = list(upids)
            c.execute("delete from roster_slots where upid = any(%s)", (ups,))
            c.execute("delete from contracts where upid = any(%s)", (ups,))
            c.execute("delete from player_events where source_file = 'league_actions' and upid = any(%s)", (ups,))
        c.execute("delete from player_events where admin = %s and upid is null", (BY,))    # window and slot-purchase events
        c.execute("delete from prospect_draft_slots where ledger_id in (select id from wb_ledger where created_by = %s)", (BY,))
        c.execute("set fbp.ledger_maintenance = 'on'")   # the ledger is append-only; test cleanup only
        c.execute("delete from wb_ledger where created_by = %s or (ref_type = 'draft_speed' and season = %s)", (BY, AWARD_SEASON))
        c.execute("reset fbp.ledger_maintenance")
        c.execute("delete from league_windows where opened_by = %s", (BY,))
        c.execute("delete from draft_award_amounts where season = %s", (AWARD_SEASON,))
        c.execute("delete from players where mlb_id between 9990000 and 9999999")

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


def run_threads(url, jobs):
    """Run fn(conn) for each job on its own connection at the same moment. Returns [(ok, value_or_exception)]."""
    barrier = threading.Barrier(len(jobs))

    def one(job):
        conn = connect(url)
        try:
            barrier.wait(timeout=5)
            try:
                return True, job(conn)
            except Exception as e:  # noqa: BLE001 - the tests inspect the type
                return False, e
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
        return list(ex.map(one, jobs))


def upids_in(obj):
    """Every value stored under a 'upid' key, anywhere in a nested payload."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "upid" and v:
                found.append(v)
            found += upids_in(v)
    elif isinstance(obj, list):
        for v in obj:
            found += upids_in(v)
    return found


# -------------------------------------------------------------- mirror, pool, constraints

def test_slots_from_mirror_and_create(world):
    c = world.conn
    prospect = ds.slots_from_mirror(c, "prospect", 2026)
    keeper = ds.slots_from_mirror(c, "keeper", 2026)
    assert len(prospect) == 52 and {s["round_type"] for s in prospect} == {"fypd", "open"}
    assert len(keeper) == 348 and sum(1 for s in keeper if s["skip_reason"] is None) == 139    # 139 draftable (2026 findings)
    assert {s["skip_reason"] for s in keeper} == {None, "keeper", "taxed", "buyin_unpaid"}
    ds.create_draft(c, "dt-mirror", SEASON, "keeper", keeper, is_test=True)
    d = c.execute("select clock_seconds, status from drafts where draft_id = 'dt-mirror'").fetchone()
    assert d == (180, "setup")
    assert c.execute("select count(*) from draft_slots where draft_id = 'dt-mirror'").fetchone()[0] == 348
    with pytest.raises(DraftError):
        ds.create_draft(c, "dt-mirror", SEASON, "keeper", keeper, is_test=True)    # same id twice


def test_pool_reasons_and_overrides(world):
    c = world.conn
    did = world.draft("pool", [["B2J", "CFL"], ["B2J", "CFL"], ["B2J"]], draft_type="prospect", start=False)
    owned = world.owned()
    farm, non_fypd = world.players(did, 1, round=1)[0], None
    non_fypd = world.players(did, 1, round=3, fypd=False)[0]

    def pool(upid, rnd=None):
        return c.execute("select eligible, reason from draft_pool(%s, %s) where upid = %s", (did, rnd, upid)).fetchone()

    assert pool(farm, 1) == (True, None)
    assert pool(non_fypd, 3) == (True, None)
    assert pool(non_fypd, 1) == (False, "Not FYPD eligible (rounds 1-2)")
    assert pool(owned)[1].startswith("Owned by ")
    mlb = c.execute("select upid from players where player_type = 'MLB' and upid not in (select upid from roster_assignments where valid_to is null) limit 1").fetchone()[0]
    assert pool(mlb)[1].startswith("Not a prospect")
    ds.set_override(c, did, non_fypd, "exclude", "retired", "zach")
    assert pool(non_fypd, 3) == (False, "Excluded by admin: retired")
    ds.set_override(c, did, non_fypd, "include", "wrongly excluded", "zach")
    assert pool(non_fypd, 1) == (True, None)             # include lifts the FYPD rule
    ds.set_override(c, did, mlb, "include", "he is a prospect", "zach")
    assert pool(mlb, 1) == (True, None)                  # ... and the player-type rule
    ds.set_override(c, did, owned, "include", "oops", "zach")
    assert pool(owned)[1].startswith("Owned by ")        # ... but never ownership
    with pytest.raises(DraftError):
        ds.set_override(c, did, farm, "exclude", "  ", "zach")
    with pytest.raises(psycopg.errors.UniqueViolation):   # a player can only be drafted once, enforced by the database
        c.execute(f"insert into {PICKS} (draft_id, overall, upid, team_abbr, source, made_by, contract_tier) values (%s,1,%s,'B2J','web','x','DC')", (did, farm))
        c.execute(f"insert into {PICKS} (draft_id, overall, upid, team_abbr, source, made_by, contract_tier) values (%s,2,%s,'CFL','web','x','DC')", (did, farm))


# -------------------------------------------------------------- 3, 4: turns and races

def test_03_same_player_at_the_same_instant_one_wins(world):
    a, b, c_ = world.teams[:3]
    did = world.draft("race", [[a, b, c_]])
    x, y = world.players(did, 2)
    results = run_threads(world.url, [lambda cn: ds.make_pick(cn, did, a, x, "web", "mgr-a", at(5)),
                                      lambda cn: ds.make_pick(cn, did, b, x, "web", "mgr-b", at(5))])
    assert [ok for ok, _ in results].count(True) == 1
    ok_a, ok_b = results[0][0], results[1][0]
    assert ok_a and not ok_b                         # only the team on the clock can win
    err = results[1][1]
    assert isinstance(err, DraftError) and (err.status == 403 or str(err).startswith("Already drafted"))
    assert [p[1] for p in world.picks(did)] == [x]
    # the next manager, now on the clock, asking for the same player: "Already drafted"
    with pytest.raises(DraftError, match=r"^Already drafted \(pick 1\)"):
        ds.make_pick(world.conn, did, b, x, "web", "mgr-b", at(6))
    assert len(world.picks(did)) == 1


def test_04_out_of_turn_is_rejected_with_403(world):
    a, b = world.teams[:2]
    did = world.draft("turn", [[a, b]])
    (x,) = world.players(did, 1)
    with pytest.raises(DraftError) as e:
        ds.make_pick(world.conn, did, b, x, "web", "mgr-b", at(3))
    assert e.value.status == 403
    assert world.picks(did) == [] and world.contract(x) is None
    with pytest.raises(DraftError):      # the runner's sources can't be used by a web caller
        ds.make_pick(world.conn, did, a, x, "autopick", "mgr-a", at(3))
    ds.pause(world.conn, did, "zach", now=at(4))
    with pytest.raises(DraftError, match="paused"):
        ds.make_pick(world.conn, did, a, x, "web", "mgr-a", at(5))


# -------------------------------------------------------------- 1, 2, 5: the clock

def test_01_02_restart_rule(world):
    a, b = world.teams[:2]
    did = world.draft("restart", [[a, b]], clock=180)
    deadline = world.clock(did)[1]
    assert deadline == at(180)
    assert world.tick(did, 1, stale=15) is None
    # bot killed with 1:30 left and back: the deadline stays exactly where the database says
    assert world.tick(did, 90, stale=15) is None
    assert world.clock(did)[1] == deadline and world.notes("draft_resumed") == []
    # the deadline passes while the bot is down: on restart the clock is extended to 1:00, no autopick
    assert world.tick(did, 400, stale=15) == "extended"
    assert world.clock(did)[1] == at(460)
    assert world.picks(did) == []
    (note,) = world.notes("draft_resumed")
    assert note["message"] == "Draft resumed, 1:00 added to the clock"
    assert world.conn.execute("select runner_heartbeat_at from draft_clock where draft_id = %s", (did,)).fetchone()[0] == at(400)
    # running normally afterwards, the 60-second warning fires once
    assert world.tick(did, 401, stale=15) == "warned" and world.tick(did, 402, stale=15) is None   # 59 seconds left
    assert len(world.notes("warning", team=a)) == 1
    assert ds.state(world.conn, did, at(406))["clock"]["runner_stale"] is False


def test_05_pause_and_resume_keep_the_time_left(world):
    a, b = world.teams[:2]
    did = world.draft("pause", [[a, b]], clock=240)
    ds.pause(world.conn, did, "zach", "bathroom", at(150))
    assert world.clock(did)[1:3] == (None, 90_000)
    assert world.tick(did, 150 + 600) is None and world.picks(did) == []       # a paused draft never autopicks
    ds.resume(world.conn, did, "zach", at(750))
    assert world.clock(did)[1] == at(750) + timedelta(seconds=90)               # 1:30, not 4:00
    assert ds.state(world.conn, did, at(750))["clock"]["seconds_left"] == 90
    (x,) = world.players(did, 1)
    p = ds.make_pick(world.conn, did, a, x, "web", "mgr-a", at(760))
    assert p["seconds_used"] == 160                                              # 150 before the pause + 10 after; the pause is free
    with pytest.raises(DraftError):
        ds.resume(world.conn, did, "zach", at(761))


# -------------------------------------------------------------- 6: search

def test_06_search(world):
    c = world.conn
    did = world.draft("search", [["B2J"]], draft_type="prospect", start=False)

    def names(q, **kw):
        return ds.search(c, did, q, **kw)

    def upids_named(norm):
        return {r[0] for r in c.execute("select upid from players where draft_norm(name) = %s", (norm,))}

    r = names("chourio")
    assert r[0]["name"] == "Jackson Chourio" and not r[0]["eligible"] and r[0]["reason"]      # ineligible players come back, with a reason
    assert {"Jaison Chourio", "Byron Chourio", "Kendry Chourio"} <= {x["name"] for x in r}
    assert {"upid", "name", "positions", "org", "level", "age", "fypd", "rank", "eligible", "reason"} <= set(r[0])

    r = names("jose rodriguez")                                                                # no accents typed
    expect = upids_named("jose rodriguez")
    assert len(expect) >= 3 and "José Rodríguez" in {x["name"] for x in r}
    assert {x["upid"] for x in r[:len(expect)]} == expect                                      # exact matches first
    assert names("JOSE RODRIGUEZ") == r

    r = names("j wood")
    assert r[0]["name"] == "James Wood" and names("j. wood") == r

    r = names("jose ramos")
    expect = upids_named("jose ramos")
    assert len(expect) >= 2                                                                    # two (here three) different players
    assert {x["upid"] for x in r[:len(expect)]} == expect
    assert len({(x["org"], x["positions"]) for x in r[:len(expect)]}) == len(expect)            # told apart by org and position

    alias = c.execute("select a.alias_normalized, a.upid from player_aliases a join players p on p.upid = a.upid "
                      "where a.alias_normalized <> draft_norm(p.name) and p.name = 'Sal Frelick' limit 1").fetchone()
    assert alias is not None and alias[1] in {x["upid"] for x in names(alias[0])}               # aliases match too

    assert names("j") == [] and names("") == []                                                 # starts at 2 characters
    # the draft ranking orders equal matches
    first, second = [x["upid"] for x in names("jose ramos")][:2]
    ds.upload_rankings(c, did, [{"upid": second, "rank": 1}], source="test")
    assert names("jose ramos")[0]["upid"] == second and names("jose ramos")[0]["rank"] == 1


# -------------------------------------------------------------- 9: adding a player

def test_09_add_a_player_mid_draft(world):
    c = world.conn
    a, b, cc = world.teams[:3]
    did = world.draft("add", [[a, b, cc]])
    req = ds.request_add_player(c, did, b, 9990001, "Test Newcomer", note="signed in January", now=at(5))
    assert req["status"] == "pending" and not req["paused_draft"] and world.status(did) == "live"
    (n,) = world.notes("add_request")
    assert n["audience"] == "admins" and n["player"] == "Test Newcomer"
    t = time.perf_counter()
    done = ds.decide_add_request(c, req["id"], True, "zach", player_type="MLB", details={"mlb_team": "SEA", "positions": "OF"}, now=at(6))
    hit = ds.search(c, did, "newcomer")
    assert time.perf_counter() - t < 2                                       # searchable at once
    assert done["status"] == "approved" and hit[0]["name"] == "Test Newcomer" and hit[0]["eligible"]
    ds.make_pick(c, did, a, done["created_upid"], "web", "mgr-a", at(7))      # draftable at once
    assert world.contract(done["created_upid"])[0] == a
    assert [e["kind"] for e in world.notes()].count("player_added") == 1
    assert world.notes("add_request_decided", team=b)[0]["approved"] is True
    kinds = [r[0] for r in c.execute("select kind from draft_events where draft_id = %s order by id", (did,))]
    assert {"add_requested", "player_added"} <= set(kinds)
    with pytest.raises(DraftError, match="already in FBP"):                   # a player FBP already has is not duplicated
        ds.request_add_player(c, did, b, c.execute("select mlb_id from players where mlb_id is not null limit 1").fetchone()[0], "Dup", now=at(8))
    with pytest.raises(DraftError, match="Say why"):
        r2 = ds.request_add_player(c, did, b, 9990002, "Test Reject", now=at(9))
        ds.decide_add_request(c, r2["id"], False, "zach", now=at(10))
    assert ds.decide_add_request(c, r2["id"], False, "zach", decision_note="not eligible", now=at(10))["status"] == "rejected"


def test_09a_on_clock_request_pauses_once_per_team(world):
    c = world.conn
    a, b = world.teams[:2]
    did = world.draft("addpause", [[a, b, a, b]], clock=180)
    r1 = ds.request_add_player(c, did, a, 9990011, "Test Pausey One", now=at(20))
    assert r1["paused_draft"] and world.status(did) == "paused" and world.clock(did)[2] == 160_000
    ds.decide_add_request(c, r1["id"], True, "zach", player_type="MLB", now=at(620))
    assert world.status(did) == "live" and world.clock(did)[1] == at(620) + timedelta(seconds=160)   # same time left
    new = c.execute("select upid from players where mlb_id = 9990011").fetchone()[0]
    r2 = ds.request_add_player(c, did, a, 9990012, "Test Pausey Two", now=at(630))                       # second request: no pause
    assert not r2["paused_draft"] and world.status(did) == "live"
    assert world.clock(did)[1] == at(620) + timedelta(seconds=160)
    r3 = ds.request_add_player(c, did, b, 9990013, "Test Pausey Three", now=at(631))                     # not on the clock: no pause
    assert not r3["paused_draft"]
    p = ds.make_pick(c, did, a, new, "web", "mgr-a", at(640))
    assert p["name"] == "Test Pausey One"


def test_09b_two_requests_for_the_same_player(world):
    a, b, cc = world.teams[:3]
    did = world.draft("adddup", [[a, b, cc]])
    results = run_threads(world.url, [lambda cn: ds.request_add_player(cn, did, b, 9990021, "Test Same", now=at(5)),
                                      lambda cn: ds.request_add_player(cn, did, cc, 9990021, "Test Same", now=at(5))])
    assert [ok for ok, _ in results].count(True) == 1
    assert isinstance([e for ok, e in results if not ok][0], DraftError)
    assert world.conn.execute("select count(*) from draft_add_requests where draft_id = %s and status = 'pending'", (did,)).fetchone()[0] == 1
    assert world.conn.execute("select count(*) from players where mlb_id = 9990021").fetchone()[0] == 0     # a request creates no player


# -------------------------------------------------------------- 10, 11: boards and autopick

def test_10_autopick_skips_ineligible_board_entries(world):
    c = world.conn
    a, b, cc = world.teams[:3]
    did = world.draft("board", [[b, cc, b, a, b, cc]], clock=180)
    p = world.players(did, 12)
    owned = world.owned()
    first, second, third = p[0], p[1], p[2]
    ds.make_pick(c, did, b, first, "web", "mgr-b", at(1))
    ds.make_pick(c, did, cc, second, "web", "mgr-c", at(2))
    ds.make_pick(c, did, b, third, "web", "mgr-b", at(3))        # a is now on the clock at pick 4
    world.board(did, a, 4, [owned, first, p[3], p[4]])             # owned, already drafted, then the first real target
    world.board(did, a, "any", [p[5]])
    assert world.tick(did, 100) is None
    assert world.tick(did, 3 + 180) == "autopick"
    assert [x[1] for x in world.picks(did)][3] == p[3]             # skipped the owned and the drafted player
    assert world.picks(did)[3][3:5] == ("autopick", 180)           # source autopick, full clock for the speed awards
    # b at pick 5: this pick's list is all ineligible, so the "Any pick" list is used
    world.board(did, b, 5, [owned, p[3]])
    world.board(did, b, "any", [p[3], p[6]])
    assert world.tick(did, 3 + 180 + 180) == "autopick"
    assert world.picks(did)[4][1] == p[6]
    # cc at pick 6: no board; the ranking is used, skipping ranked players who are gone
    rank = ds.upload_rankings(c, did, [{"upid": p[3], "rank": 1}, {"upid": p[7], "rank": 2}, {"upid": p[8], "rank": 3}], source="test")
    assert rank["saved"]
    assert world.tick(did, 3 + 180 * 3) == "autopick"
    assert world.picks(did)[5][1] == p[7]
    assert world.status(did) == "complete"
    for row in world.picks(did):                                    # contracts match picks
        assert world.contract(row[1])[0] == row[2]


def test_10a_boards_are_private(world):
    a, b, cc = world.teams[:3]
    did = world.draft("private", [[a, b, cc]])
    p = world.players(did, 3)
    world.board(did, a, 1, [p[0], p[1]])
    world.board(did, a, "any", [p[2]])
    with pytest.raises(DraftError) as e:
        ds.set_board(world.conn, did, b, 1, [p[0]])               # b doesn't own pick 1
    assert e.value.status == 403
    mine = ds.state(world.conn, did, at(5), team=a)
    theirs = ds.state(world.conn, did, at(5), team=b)
    anon = ds.state(world.conn, did, at(5))
    assert upids_in(mine["my_board"]) == [] and set(mine["my_board"]) == {f"{did}:1", "any"}
    assert "my_board" in theirs and theirs["my_board"] == {} and "my_board" not in anon
    for payload in (theirs, anon):
        assert not (set(upids_in(payload)) & set(p))
        assert f"{did}:1" not in json.dumps(payload)
    assert ds.get_board(world.conn, did, a)["any"] == [p[2]] and ds.get_board(world.conn, did, b) == {}


def test_11_no_ranking_and_empty_board_pauses_and_alerts(world):
    c = world.conn
    a, b = world.teams[:2]
    did = world.draft("noauto", [[a, b]], clock=180)
    assert world.tick(did, 181) == "paused"
    assert world.status(did) == "paused" and world.picks(did) == [] and world.clock(did)[1:3] == (None, 180_000)
    (alert,) = world.notes("admin_alert")
    assert alert["audience"] == "admins" and "nobody to autopick" in alert["message"]
    assert world.notes("draft_paused")
    # Zach uploads a ranking and resumes: the team gets a full clock, then the ranking is used
    (x,) = world.players(did, 1)
    ds.upload_rankings(c, did, [{"upid": x, "rank": 1}])
    ds.resume(c, did, "zach", at(200))
    assert world.tick(did, 200 + 181) == "autopick" and world.picks(did)[0][1] == x


def test_11b_forklift(world):
    c = world.conn
    a, b = world.teams[:2]
    did = world.draft("fork", [[a, b, a]], clock=180, start=False)
    ds.set_forklift(c, did, a, True, "zach", at(0))
    ds.start(c, did, "zach", at(0), check=False)
    assert world.clock(did)[1] == at(10)                              # forklift teams get 10 seconds
    assert world.tick(did, 9) is None
    assert world.tick(did, 10) == "forklift_off"                      # empty board: forklift turns itself off, normal clock
    assert c.execute("select count(*) from draft_forklift where draft_id = %s", (did,)).fetchone()[0] == 0
    assert world.status(did) == "live" and world.clock(did)[1] == at(10) + timedelta(seconds=180)
    assert world.notes("forklift_off", team=a) and world.picks(did) == []
    # forklift with a board target: board-only autopick, source forklift, full clock counted
    ds.set_forklift(c, did, a, True, "zach", at(11))
    assert world.clock(did)[1] == at(21)                              # turning it on shortens the running clock
    (x,) = world.players(did, 1)
    world.board(did, a, 1, [x])
    assert world.tick(did, 21) == "forklift"
    assert world.picks(did)[0][1:5] == (x, a, "forklift", 180)


# -------------------------------------------------------------- 12a: 2 Time

def test_12a_two_time_locks_in_together(world):
    c = world.conn
    a, b = world.teams[:2]
    did = world.draft("two", [[a, a, b]])
    assert world.notes("on_clock", team=a)[0]["tag"] == "2 Time" and world.notes("on_clock", team=a)[0]["run_size"] == 2
    st = ds.state(c, did, at(1))
    assert st["on_clock"]["run_tag"] == "2 Time" and [s["run_index"] for s in st["slots"]] == [1, 2, 1]
    x, y = world.players(did, 2)
    mark = world.last_note()
    picks = ds.make_picks(c, did, a, [x, y], "web", "mgr-a", at(30))
    assert [p["overall"] for p in picks] == [1, 2] and world.clock(did)[0] == 3
    assert [p["seconds_used"] for p in picks] == [30, 0]              # each timed from when it went on the clock
    assert world.clock(did)[1] == at(30) + timedelta(seconds=180)
    (msg,) = world.notes("pick_made", after=mark)
    assert msg["run_size"] == 2 and msg["tag"] == "2 Time" and [p["upid"] for p in msg["picks"]] == [x, y]
    assert [n["team"] for n in world.notes("on_clock", after=mark)] == [b]    # one DM, to the next team


def test_12a_all_or_nothing_and_partial_run(world):
    c = world.conn
    a, b = world.teams[:2]
    did = world.draft("twofail", [[a, a, b]])
    (x,) = world.players(did, 1)
    with pytest.raises(DraftError, match="Owned by"):
        ds.make_picks(c, did, a, [x, world.owned()], "web", "mgr-a", at(30))
    assert world.picks(did) == [] and world.contract(x) is None and world.clock(did)[0] == 1
    with pytest.raises(DraftError):                                   # asking for 3 picks of a 2-run: second pick belongs to a, third to b
        ds.make_picks(c, did, a, world.players(did, 3), "web", "mgr-a", at(30))
    assert world.picks(did) == []
    # a 3 Time with only 1 of 3 locked in: the next pick in the run is on the clock, nobody is re-notified
    did3 = world.draft("three", [[a, a, a, b]])
    mark = world.last_note()
    (y,) = world.players(did3, 1)
    ds.make_pick(c, did3, a, y, "web", "mgr-a", at(10))
    assert world.clock(did3)[0] == 2 and world.clock(did3)[1] == at(10) + timedelta(seconds=180)
    assert world.notes("on_clock", after=mark) == []
    assert ds.state(c, did3, at(11))["on_clock"]["run_size"] == 2


# -------------------------------------------------------------- 13, 14: corrections

def _snapshot(world, did, skip):
    return [r for r in world.picks(did) if r[0] != skip]


def test_13_swap_player(world):
    c = world.conn
    a, b, cc = world.teams[:3]
    did = world.draft("swap", [[a, b, cc, a, b, cc, a, b]])
    p = world.players(did, 9)
    for i in range(7):
        ds.make_pick(c, did, [a, b, cc][i % 3], p[i], "web", "mgr", at(10 * (i + 1)))
    assert world.clock(did)[0] == 8
    before = _snapshot(world, did, 2)
    ds.swap_player(c, did, 2, p[7], "zach", "wrong player clicked", at(100))
    assert dict((r[0], r[1]) for r in world.picks(did))[2] == p[7]                 # pick 2 shows the new player
    assert _snapshot(world, did, 2) == before                                        # the other picks are unchanged
    assert world.contract(p[7])[0] == b and world.contract(p[1]) is None            # contract moved with the pick
    assert c.execute("select eligible from draft_pool(%s) where upid = %s", (did, p[1])).fetchone()[0] is True   # old player is back
    assert world.clock(did)[0] == 8
    with pytest.raises(DraftError, match="Already drafted"):                         # would collide with a later pick
        ds.swap_player(c, did, 3, p[4], "zach", "oops", at(101))
    with pytest.raises(DraftError, match="reason"):
        ds.swap_player(c, did, 3, p[8], "zach", " ", at(101))
    assert _snapshot(world, did, 2) == before
    assert {"swap"} <= {r[0] for r in c.execute("select kind from draft_events where draft_id = %s", (did,))}
    assert world.notes("correction")


def test_14_void_gives_a_makeup_pick(world):
    c = world.conn
    a, b, cc = world.teams[:3]
    did = world.draft("void", [[a, b, cc, a, b, cc, a, b]])
    p = world.players(did, 9)
    for i in range(7):
        ds.make_pick(c, did, [a, b, cc][i % 3], p[i], "web", "mgr", at(10 * (i + 1)))
    before = _snapshot(world, did, 2)
    ds.void_pick(c, did, 2, "zach", "wrong team's player", at(100))
    assert world.clock(did)[0] == 8 and world.status(did) == "live"                 # the current pick is not disturbed
    assert _snapshot(world, did, 2) == before and 2 not in [r[0] for r in world.picks(did)]
    assert world.contract(p[1]) is None
    mark = world.last_note()
    ds.make_pick(c, did, b, p[8], "web", "mgr-b", at(120))
    # right after the current pick: the voided slot's team (b) is on the clock with a full clock
    assert world.clock(did)[0] == 2 and world.clock(did)[1] == at(120) + timedelta(seconds=180)
    assert [n["team"] for n in world.notes("on_clock", after=mark)] == [b]
    assert ds.state(c, did, at(121))["slots"][1]["makeup"] is True
    ds.make_pick(c, did, b, p[1], "web", "mgr-b", at(130))                          # b can take the same player again
    assert world.status(did) == "complete" and len(world.picks(did)) == 8
    kinds = [r[0] for r in c.execute("select kind from draft_events where draft_id = %s", (did,))]
    assert "void" in kinds and "makeup_pick" in kinds


def test_undo_last_pick(world):
    c = world.conn
    a, b = world.teams[:2]
    did = world.draft("undo", [[a, b]])
    x, y = world.players(did, 2)
    ds.make_pick(c, did, a, x, "web", "mgr-a", at(10))
    ds.make_pick(c, did, b, y, "web", "mgr-b", at(20))
    assert world.status(did) == "complete"
    ds.undo_last_pick(c, did, "zach", "misclick", at(100))
    assert world.status(did) == "live" and world.clock(did)[0] == 2 and world.clock(did)[1] == at(100) + timedelta(seconds=180)
    assert world.contract(y) is None and world.contract(x)[0] == a and [r[0] for r in world.picks(did)] == [1]
    ds.undo_last_pick(c, did, "zach", "again", at(101))
    assert world.picks(did) == [] and world.clock(did)[0] == 1
    with pytest.raises(DraftError):
        ds.undo_last_pick(c, did, "zach", "nothing left", at(102))


# -------------------------------------------------------------- the Prospect Draft contracts

def test_prospect_draft_fills_prepaid_slots(world):
    c = world.conn
    a, b, cc = world.teams[:3]
    did = world.draft("pros", [(1, [a, b]), (3, [a, cc])], draft_type="prospect", clock=240, start=False)
    world.prepay(a, "BC")
    world.prepay(a, "DC")
    world.prepay(b, "BC")
    with pytest.raises(DraftError, match="not ready|Not ready"):
        ds.start(c, did, "zach", at(0))                       # preflight: cc has a pick in the order and no prepaid slot
    world.prepay(cc, "DC")
    assert ds.preflight(c, did) == []
    ds.start(c, did, "zach", at(0))
    fy = world.players(did, 3, round=1)
    plain = world.players(did, 2, round=3, fypd=False)
    with pytest.raises(DraftError, match="Not FYPD eligible"):
        ds.make_pick(c, did, a, c.execute("select upid from draft_pool(%s, 3) where eligible and fypd is not true limit 1", (did,)).fetchone()[0], "web", "a", at(5))
    ds.make_pick(c, did, a, fy[0], "web", "mgr-a", at(10))
    assert world.contract(fy[0]) == (a, "prospect", None, "BC", "prospect_draft", 1)
    ds.make_pick(c, did, b, fy[1], "web", "mgr-b", at(20))
    ds.make_pick(c, did, a, plain[0], "web", "mgr-a", at(30))
    assert world.contract(plain[0]) == (a, "prospect", None, "DC", "prospect_draft", 3)
    filled = c.execute("select team_abbr, slot_type from prospect_draft_slots where filled_upid is not null and ledger_id in "
                       "(select id from wb_ledger where created_by = %s) order by team_abbr, slot_type", (BY,)).fetchall()
    assert filled == [(a, "BC"), (a, "DC"), (b, "BC")]
    # swap in a round-1 pick keeps the slot filled; void frees the slot and the player
    ds.swap_player(c, did, 1, fy[2], "zach", "same-name mixup", at(40))
    assert c.execute("select filled_upid from prospect_draft_slots where filled_upid = %s", (fy[2],)).fetchone() is not None
    assert c.execute("select filled_upid from prospect_draft_slots where filled_upid = %s", (fy[0],)).fetchone() is None
    ds.void_pick(c, did, 3, "zach", "test", at(50))
    assert c.execute("select count(*) from prospect_draft_slots where filled_upid = %s", (plain[0],)).fetchone()[0] == 0
    ds.make_pick(c, did, cc, plain[1], "web", "mgr-c", at(60))
    assert world.contract(plain[1])[3] == "DC"
    ds.make_pick(c, did, a, plain[0], "web", "mgr-a", at(70))                      # the makeup pick refills the freed DC slot
    assert world.status(did) == "complete"


def test_pick_without_a_prepaid_slot_is_refused(world):
    a, b = world.teams[:2]
    did = world.draft("noslot", [(3, [a, b])], draft_type="prospect", start=False)
    world.prepay(a, "DC")
    ds.start(world.conn, did, "zach", at(0), check=False)
    (x,) = world.players(did, 1, round=3)
    ds.make_pick(world.conn, did, a, x, "web", "mgr-a", at(5))
    (y,) = world.players(did, 1, round=3, offset=1)
    with pytest.raises(DraftError, match="no open DC draft slot"):
        ds.make_pick(world.conn, did, b, y, "web", "mgr-b", at(6))
    assert [r[0] for r in world.picks(did)] == [1] and world.clock(did)[0] == 2
    # the runner doesn't retry forever: autopick for b fails, so the draft pauses and Zach is told
    ds.upload_rankings(world.conn, did, [{"upid": y, "rank": 1}])
    assert world.tick(did, 5 + 241) == "paused" and world.status(did) == "paused"
    assert "no open DC draft slot" in world.notes("admin_alert")[0]["message"]


def test_preflight_ranking_checks(world):
    c = world.conn
    a = world.teams[0]
    did = world.draft("pre", [[a]], start=False)
    (x,) = world.players(did, 1)
    ds.upload_rankings(c, did, [{"upid": x, "rank": 1}, {"upid": world.owned(), "rank": 2}])
    problems = ds.preflight(c, did)
    assert any("is not eligible" in p for p in problems) and any("missing from the ranking" in p for p in problems)
    with pytest.raises(DraftError, match="Not ready"):
        ds.start(c, did, "zach", at(0))
    assert world.status(did) == "setup"


# -------------------------------------------------------------- ranking upload

def test_upload_rankings_matches_and_reports(world):
    c = world.conn
    did = world.draft("rank", [["B2J"]], draft_type="prospect", start=False)
    mlb = c.execute("select upid, mlb_id from players where player_type = 'Farm' and mlb_id is not null "
                    "and mlb_id in (select mlb_id from players group by 1 having count(*) = 1) limit 1").fetchone()
    rows = [{"upid": mlb[0], "rank": 1}, {"mlb_id": mlb[1], "rank": 2}, {"name": "Gage Wood", "rank": 3},
            {"name": "Jose Ramos", "rank": 4}, {"name": "Nobody McNoname", "rank": 5}, {"upid": "999999", "rank": "x"}]
    res = ds.upload_rankings(c, did, rows)
    assert not res["saved"] and res["matched"] == 2          # row 2 repeats row 1's player; nothing is saved while rows are unmatched
    reasons = " | ".join(u["reason"] for u in res["unmatched"])
    assert "same player" in reasons and "several players" in reasons and "no player with that name" in reasons and "rank must be" in reasons
    assert c.execute("select count(*) from draft_rankings where draft_id = %s", (did,)).fetchone()[0] == 0
    ok = ds.upload_rankings(c, did, [{"upid": mlb[0], "rank": 1}, {"name": "Gage Wood", "rank": 2}], source="csv")
    assert ok["saved"] and c.execute("select count(*) from draft_rankings where draft_id = %s", (did,)).fetchone()[0] == 2


# -------------------------------------------------------------- 13 (awards): speed

def test_13_awards_rule_ties_and_amounts():
    amounts = [30, 25, 20, 15, 10, 5]
    rows = [(f"T{i}", 100 + 10 * i, 10) for i in range(8)]                    # averages 10, 11, ... 17: no ties
    got = ds.speed_standings(rows, amounts)
    assert [(s["team"], s["place"], s["amount"]) for s in got] == [("T0", 1, 30), ("T1", 2, 25), ("T2", 3, 20), ("T3", 4, 15),
                                                                   ("T4", 5, 10), ("T5", 6, 5), ("T6", 7, 0), ("T7", 8, 0)]
    assert sum(s["amount"] for s in got) == 105
    tied = ds.speed_standings([("A", 50, 5), ("B", 100, 10), ("C", 20, 2)], amounts)    # A, B and C all average 10
    assert {s["amount"] for s in tied} == {(30 + 25 + 20) // 3} and {s["place"] for s in tied} == {1}
    rows6 = [(f"T{i}", 100 + 10 * i, 10) for i in range(5)] + [("X", 150, 10), ("Y", 300, 20), ("Z", 200, 10)]   # X and Y tie for 6th (avg 15)
    got6 = {s["team"]: s for s in ds.speed_standings(rows6, amounts)}
    assert got6["X"]["place"] == got6["Y"]["place"] == 6 and got6["X"]["amount"] == got6["Y"]["amount"] == 2     # $5 split, rounded down
    assert got6["Z"]["amount"] == 0
    assert ds.speed_standings([("A", 0, 0)], amounts) == []                       # a team with no picks isn't ranked


def test_13_speed_awards_paid_once_after_the_second_draft(world):
    c = world.conn
    t = world.teams[:8]
    c.execute("insert into draft_award_amounts (season, place, amount) select %s, place, amount from draft_award_amounts where season = 2027", (AWARD_SEASON,))
    # Prospect Draft (a real draft, finished): picks inserted with chosen times, carried into the combined average
    pd = world.draft("aw-p", [t], draft_type="prospect", clock=240, is_test=False, season=AWARD_SEASON, start=False)
    prospect_secs = [200, 30, 40, 50, 60, 70, 80, 90]
    ups = [r[0] for r in c.execute("select upid from players order by upid::bigint limit 24")]
    for i, team in enumerate(t):
        c.execute(f"insert into {PICKS} (draft_id, overall, upid, team_abbr, source, made_by, seconds_used, contract_tier) "
                  "values (%s,%s,%s,%s,'web','x',%s,'DC')", (pd, i + 1, ups[i], team, prospect_secs[i]))
    c.execute("update drafts set status = 'complete' where draft_id = %s", (pd,))
    # a test draft in the same season, very fast for the last team: it must not count
    td = world.draft("aw-t", [t], draft_type="keeper", is_test=True, season=AWARD_SEASON, start=False)
    c.execute(f"insert into {PICKS} (draft_id, overall, upid, team_abbr, source, made_by, seconds_used, contract_tier) "
              "values (%s,8,%s,%s,'web','x',0,'VC-1')", (td, ups[8], t[7]))
    assert ds.pay_speed_awards(c, AWARD_SEASON, at(0)) == []                     # the Keeper Draft isn't complete: nothing pays
    # Keeper Draft: made through make_pick with controlled times; t[7] is a forklift team (full clock per pick)
    kd = world.draft("aw-k", [t], draft_type="keeper", clock=180, is_test=False, season=AWARD_SEASON, start=False)
    ds.set_forklift(c, kd, t[7], True, "zach", at(0))
    ds.start(c, kd, "zach", at(0), check=False)
    keeper_used = [10, 30, 40, 50, 60, 70, 80, 5]                                 # t[7]'s 5 seconds is counted as 180
    players = world.players(kd, 8)
    now = 0
    for i, team in enumerate(t):
        now += keeper_used[i]
        p = ds.make_pick(c, kd, team, players[i], "web", f"mgr-{team}", at(now))
        if i < 7:
            assert p["seconds_used"] == keeper_used[i]
            assert c.execute("select count(*) from wb_ledger where kind = 'draft_award'").fetchone()[0] == 0   # nothing before the end
        else:
            assert p["seconds_used"] == 180                                      # forklift: the full clock
    assert world.status(kd) == "complete"
    # combined averages: (prospect + keeper) / 2 -> t1 (30+30) ... t0 (200+10) is the slowest of the first seven
    paid = c.execute("select team_abbr, amount from wb_ledger where kind = 'draft_award' and season = %s order by amount desc, team_abbr",
                     (AWARD_SEASON,)).fetchall()
    avg = {team: (prospect_secs[i] + (keeper_used[i] if i < 7 else 180)) / 2 for i, team in enumerate(t)}
    order = sorted(t, key=lambda x: (avg[x], x))
    assert [x[0] for x in paid] == order[:6] and [x[1] for x in paid] == [30, 25, 20, 15, 10, 5]
    assert t[7] not in [x[0] for x in paid] and t[0] not in [x[0] for x in paid]
    final = c.execute("select payload from notifications where kind = 'speed_awards' and payload->>'draft_id' = %s order by id desc",
                      (f"season-{AWARD_SEASON}",)).fetchone()[0]
    assert final["final"] is True and [s["team"] for s in final["paid"]] == order[:6]
    again = ds.pay_speed_awards(c, AWARD_SEASON, at(1000))                         # running it twice pays nothing more
    assert again == [] and c.execute("select count(*) from wb_ledger where kind = 'draft_award' and season = %s", (AWARD_SEASON,)).fetchone()[0] == 6


def test_13_test_drafts_never_pay(world):
    c = world.conn
    t = world.teams[:3]
    c.execute("insert into draft_award_amounts (season, place, amount) values (%s, 1, 30)", (AWARD_SEASON,))
    for name, typ in (("tp", "prospect"), ("tk", "keeper")):
        did = world.draft(f"aw-{name}", [t], draft_type=typ, is_test=True, season=AWARD_SEASON, start=False)
        c.execute(f"insert into {PICKS} (draft_id, overall, upid, team_abbr, source, made_by, seconds_used, contract_tier) "
                  "values (%s,1,%s,%s,'web','x',1,%s)", (did, "2750" if typ == "prospect" else "3468", t[0], "DC" if typ == "prospect" else "VC-1"))
        c.execute("update drafts set status = 'complete' where draft_id = %s", (did,))
    assert ds.pay_speed_awards(c, AWARD_SEASON, at(0)) == []
    assert c.execute("select count(*) from wb_ledger where kind = 'draft_award' and season = %s", (AWARD_SEASON,)).fetchone()[0] == 0
