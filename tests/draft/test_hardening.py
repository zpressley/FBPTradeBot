"""Draft room hardening tests (the reviewer's findings). Same setup as test_service.py: local Postgres with
migrations applied and the JSON import loaded; every test cleans up after itself through the `world` fixture."""

import importlib.util
import json
import re
from datetime import timedelta

import psycopg
import pytest

from draft import service as ds
from draft.service import DraftError
from league import actions
from league.actions import RuleError
from tests.draft.test_service import (  # noqa: F401  (fixtures + helpers shared with test_service.py)
    AWARD_SEASON, BY, NEVER, PICKS, REPO_ROOT, SEASON, T0, at, connect, db_url, world,
)


# -------------------------------------------------------------- 1: outage time is never an autopick

def test_outage_time_never_becomes_an_autopick(world):
    a, b = world.teams[:2]
    did = world.draft("outage", [[a, b]], clock=180)
    x, y = world.players(did, 2)
    world.board(did, a, "any", [x])                      # a has an autopick available: it must not be used
    # the runner was down: its last heartbeat was at(0) and the deadline (at(180)) passed long ago
    ds.pause(world.conn, did, "zach", now=at(400))
    assert world.clock(did)[2] == 60_000                 # at least 60 s is stored, not 0
    ds.resume(world.conn, did, "zach", now=at(500))
    assert world.clock(did)[1] == at(560)
    assert world.tick(did, 501, stale=15) in (None, "warned")   # the heartbeat was written by resume: nothing autopicked
    assert world.picks(did) == []
    assert world.tick(did, 510, stale=15) in (None, "warned")
    assert world.picks(did) == []
    # a stored remainder under 1 s (older data, or a pause at the last instant) also resumes with 60 s
    ds.pause(world.conn, did, "zach", now=at(560))
    world.conn.execute("update draft_clock set paused_remaining_ms = 300 where draft_id = %s", (did,))
    ds.resume(world.conn, did, "zach", now=at(700))
    assert world.clock(did)[1] == at(760)
    # a healthy runner and plenty of time left: stored exactly as it is
    ds.pause(world.conn, did, "zach", now=at(710))
    assert world.clock(did)[2] == 50_000


# -------------------------------------------------------------- 2: an unexpected error pauses, never loops

def test_unexpected_autopick_error_pauses_and_alerts(world, monkeypatch):
    a, b = world.teams[:2]
    did = world.draft("boom", [[a, b]], clock=180)
    world.players(did, 2)

    def boom(*args, **kwargs):
        raise KeyError("surprise")

    monkeypatch.setattr(ds, "_autopick_candidate", boom)
    mark = world.last_note()
    assert world.tick(did, 200) == "paused"
    assert world.status(did) == "paused" and world.picks(did) == []
    (alert,) = world.notes("admin_alert", after=mark)
    assert "KeyError" in alert["message"] and a in alert["message"]
    assert world.conn.execute("select runner_heartbeat_at from draft_clock where draft_id = %s", (did,)).fetchone()[0] == at(200)
    assert world.clock(did)[2] == 180_000                  # a full clock for the team when it resumes
    assert world.tick(did, 201) is None                    # paused: the runner does not retry every second
    assert len(world.notes("admin_alert", after=mark)) == 1


# -------------------------------------------------------------- 3: the cutover rename

def _migration_pool_sql():
    text = (REPO_ROOT / "db" / "migrations" / "0004_draft.sql").read_text()
    return text[text.index("create function draft_pool("):].strip()


def test_pool_function_copy_in_rename_sql_matches_the_migration():
    rename = (REPO_ROOT / "scripts" / "db" / "cutover_draft_rename.sql").read_text()
    copy = rename[rename.index("create or replace function draft_pool("):].strip()
    expected = _migration_pool_sql().replace("create function", "create or replace function", 1).replace("draft_picks_live", "draft_picks")
    assert copy == expected


def test_cutover_rename_sql_leaves_a_working_draft_room(world, db_url, monkeypatch):
    a, b = world.teams[:2]
    did = world.draft("rename", [[a, b]], start=False)
    (x,) = world.players(did, 1)
    rename = (REPO_ROOT / "scripts" / "db" / "cutover_draft_rename.sql").read_text()
    assert not re.search(r"^\s*(begin|commit)\s*;", rename, re.I | re.M)      # the caller owns the transaction
    tx = psycopg.connect(db_url)                                              # NOT autocommit: rolled back below
    try:
        tx.execute(rename)
        names = {r[0] for r in tx.execute("select tablename from pg_tables where schemaname = 'public' and tablename like 'draft_picks%'")}
        assert names == {"draft_picks", "draft_picks_mirror_2026"}
        assert tx.execute("select count(*) from draft_picks_mirror_2026").fetchone()[0] > 0           # the 2026 order, kept
        assert tx.execute("select count(*) from draft_pool(%s) where eligible", (did,)).fetchone()[0] > 0
        monkeypatch.setattr(ds, "PICKS_TABLE", "draft_picks")                                         # the one-line change
        ds.start(tx, did, "zach", at(0), check=False)
        ds.make_pick(tx, did, a, x, "web", "mgr-a", at(5))
        assert tx.execute("select upid from draft_picks where draft_id = %s", (did,)).fetchone()[0] == x
        assert tx.execute("select eligible, reason from draft_pool(%s) where upid = %s", (did, x)).fetchone() == (False, "Already drafted (pick 1)")
        assert ds.state(tx, did, at(6))["ticker"][0]["upid"] == x
    finally:
        tx.rollback()
        tx.close()
    assert world.conn.execute("select to_regclass('draft_picks_live')").fetchone()[0] is not None   # nothing was committed
    assert world.conn.execute("select count(*) from draft_picks where season = 2026").fetchone()[0] > 0


def test_service_names_the_picks_table_in_one_place():
    source = (REPO_ROOT / "draft" / "service.py").read_text()
    assert source.count('"draft_picks_live"') == 1 and 'PICKS_TABLE = "draft_picks_live"' in source
    assert not re.search(r"(from|join|into|update)\s+draft_picks_live", source)           # SQL uses @picks


# -------------------------------------------------------------- 4: admin picks are not timed

def test_admin_pick_records_no_seconds_and_stays_out_of_the_speed_average(world):
    a, b = world.teams[:2]
    did = world.draft("admin", [[a, b]], clock=180)
    x, y = world.players(did, 2)
    ds.make_pick(world.conn, did, a, x, "admin", "zach", at(5))
    ds.make_pick(world.conn, did, b, y, "web", "mgr-b", at(30))
    rows = {p[2]: p for p in world.picks(did)}
    assert rows[a][3] == "admin" and rows[a][4] is None
    assert rows[b][4] == 25                               # timed from when it went on the clock (the admin pick, at 5)
    speed = {t: (s, n) for t, s, n in ds.speed_rows(world.conn, SEASON, True)}
    assert a not in speed and speed[b] == (25, 1)


# -------------------------------------------------------------- 5: reversing a draft pick goes through league.actions

def test_upgraded_draft_pick_cannot_be_undone(world):
    c = world.conn
    a, b = world.teams[:2]
    did = world.draft("upgraded", [(3, [a, b])], draft_type="prospect", start=False)
    world.prepay(a, "DC")
    world.prepay(b, "DC")
    ds.start(c, did, "zach", at(0))
    x, y = world.players(did, 2, round=3)
    ds.make_pick(c, did, a, x, "web", "mgr-a", at(10))
    actions.wb_credit(c, a, 100, "opening_balance", "PAD", SEASON, created_by=BY)
    actions.upgrade_prospect(c, x, "PC", created_by=BY, season=SEASON, now=at(20))          # DC -> PC after the draft
    for fn in (lambda: ds.undo_last_pick(c, did, "zach", "misclick", at(30)),
               lambda: ds.void_pick(c, did, 1, "zach", "misclick", at(30)),
               lambda: ds.swap_player(c, did, 1, y, "zach", "misclick", at(30))):
        with pytest.raises(DraftError, match=r"PC, not the DC the pick gave") as e:
            fn()
        assert e.value.status == 409 and "upgraded" in str(e.value)
    assert world.contract(x)[3] == "PC" and [p[1] for p in world.picks(did)] == [x]     # nothing changed
    assert c.execute("select count(*) from prospect_draft_slots where filled_upid = %s", (x,)).fetchone()[0] == 1


def test_reverse_draft_pick_rules(world):
    c = world.conn
    a, b = world.teams[:2]
    did = world.draft("reverse", [[a, b]])
    x, y = world.players(did, 2)
    ds.make_pick(c, did, a, x, "web", "mgr-a", at(10))
    ds.make_pick(c, did, b, y, "web", "mgr-b", at(20))
    cid = lambda u: c.execute("select id from contracts where upid = %s and valid_to is null", (u,)).fetchone()[0]  # noqa: E731

    def reverse(upid, team, **kw):
        kw.setdefault("expected_tier", "VC-1")
        with c.transaction():
            return actions.reverse_draft_pick(c, upid, team, created_by=BY, season=SEASON, now=at(50), **kw)

    for dirty, clean, msg in (
            ("il_tag = true, il_tag_season = 2027, il_tag_family = 'VC', il_tag_team = %s", "il_tag = false, il_tag_season = null, il_tag_family = null, il_tag_team = null", "IL-tagged"),
            ("rat_count = 1", "rat_count = 0", "RAT"),
            ("bc_decision = 'retained', bc_decision_season = 2027", "bc_decision = null, bc_decision_season = null", "BC decision")):
        c.execute("update contracts set " + dirty.replace("%s", f"'{a}'") + " where id = %s", (cid(x),))
        with pytest.raises(RuleError, match=msg):
            reverse(x, a)
        c.execute("update contracts set " + clean + " where id = %s", (cid(x),))
    with pytest.raises(RuleError, match="not on"):
        reverse(x, b)
    with pytest.raises(RuleError, match="not the TC-1 the pick gave"):
        reverse(x, a, expected_tier="TC-1")
    with pytest.raises(RuleError, match="needs the tier"):
        reverse(x, a, expected_tier=None)
    c.execute("update contracts set acquired_via = 'pad' where id = %s", (cid(x),))
    with pytest.raises(RuleError, match="not a draft pick"):
        reverse(x, a)
    c.execute("update contracts set acquired_via = 'keeper_draft' where upid = %s and valid_to is null", (x,))
    contract_id = cid(x)
    reverse(x, a)
    row = c.execute("select valid_to, end_reason from contracts where id = %s", (contract_id,)).fetchone()
    assert row[0] is not None and row[1] == "voided" and world.contract(x) is None
    assert c.execute("select count(*) from roster_slots where upid = %s", (x,)).fetchone()[0] == 0
    ev = c.execute("select update_type, owner_abbr from player_events where upid = %s and update_type = 'draft_pick_reversed'", (x,)).fetchone()
    assert ev == ("draft_pick_reversed", a)


# -------------------------------------------------------------- 6: runner stale pause

def test_pause_if_runner_stale(world):
    a, b = world.teams[:2]
    did = world.draft("stale", [[a, b]], clock=180)
    world.players(did, 2)
    assert ds.state(world.conn, did, at(5))["clock"]["runner_stale"] is False
    assert ds.pause_if_runner_stale(world.conn, did, at(5)) is False and world.status(did) == "live"
    assert ds.state(world.conn, did, at(100))["clock"]["runner_stale"] is True        # the API keeps reporting it
    mark = world.last_note()
    assert ds.pause_if_runner_stale(world.conn, did, at(200)) is True                  # deadline (180) already passed
    assert world.status(did) == "paused" and world.clock(did)[2] == 60_000             # the 60 s floor
    assert world.picks(did) == []
    assert [n["kind"] for n in world.notes(after=mark)] == ["draft_paused", "admin_alert"]
    assert ds.pause_if_runner_stale(world.conn, did, at(201)) is False                  # already paused
    ds.resume(world.conn, did, "zach", at(300))
    assert world.clock(did)[1] == at(360)


# -------------------------------------------------------------- 7: pool check shows the FYPD rule

def test_pool_without_a_round_carries_the_fypd_reason(world):
    c = world.conn
    a, b = world.teams[:2]
    did = world.draft("fypd", [[a, b], [a, b], [a, b]], draft_type="prospect", start=False)
    (fy,) = world.players(did, 1, round=1)
    (plain,) = world.players(did, 1, round=3, fypd=False)
    owned = world.owned()
    mlb = c.execute("select upid from players where player_type = 'MLB' and upid not in "
                    "(select upid from roster_assignments where valid_to is null) limit 1").fetchone()[0]

    def row(u):
        return c.execute("select eligible, reason, eligible_rounds, round_note from draft_pool(%s) where upid = %s", (did, u)).fetchone()

    assert row(fy) == (True, None, "all", None)
    assert row(plain) == (True, None, "open_rounds_only", "Not FYPD eligible (rounds 1-2)")
    assert row(owned)[2:] == ("none", None) and row(owned)[0] is False
    assert row(mlb)[2] == "none"
    assert c.execute("select eligible, reason from draft_pool(%s, 1) where upid = %s", (did, plain)).fetchone() == (False, "Not FYPD eligible (rounds 1-2)")
    ds.set_override(c, did, plain, "include", "signed as an FYPD player", "zach")
    assert row(plain)[2] == "all"
    kdid = world.draft("fypd-k", [[a, b]], draft_type="keeper", start=False)
    assert c.execute("select distinct eligible_rounds from draft_pool(%s) where eligible", (kdid,)).fetchall() == [("all",)]


def test_preflight_applies_the_fypd_rule_to_fypd_slots(world):
    c = world.conn
    a, b = world.teams[:2]
    did = world.draft("fypd-pre", [[a, b], [a]], draft_type="prospect", start=False)      # three FYPD picks
    plain = world.players(did, 3, round=3, fypd=False)
    fy = world.players(did, 3, round=1)
    rows = lambda ups: [{"upid": u, "rank": i} for i, u in enumerate(ups, start=1)]  # noqa: E731
    ds.upload_rankings(c, did, rows(plain))
    assert any("FYPD-eligible" in p and "0 FYPD-eligible player(s) for 3 FYPD pick(s)" in p for p in ds.preflight(c, did))
    ds.upload_rankings(c, did, rows(plain + fy))
    assert not any("FYPD-eligible" in p for p in ds.preflight(c, did))


# -------------------------------------------------------------- 9: no corrections after the awards are paid

def test_corrections_refused_after_awards_are_paid(world):
    c = world.conn
    a, b, cc = world.teams[:3]
    did = world.draft("paid", [[a, b, cc]], clock=180, is_test=False, season=AWARD_SEASON)
    x, y, z = world.players(did, 3)
    ds.make_pick(c, did, a, x, "web", "mgr-a", at(10))
    ds.make_pick(c, did, b, y, "web", "mgr-b", at(20))
    ds.undo_last_pick(c, did, "zach", "before the payout is fine", at(25))              # corrections work before payout
    ds.make_pick(c, did, b, y, "web", "mgr-b", at(30))
    actions.wb_credit(c, a, 5, "draft_award", "IN_SEASON", AWARD_SEASON, created_by=BY, ref_type="draft_speed", ref_id=str(AWARD_SEASON))
    before = world.picks(did)
    for fn in (lambda: ds.undo_last_pick(c, did, "zach", "late", at(40)),
               lambda: ds.swap_player(c, did, 1, z, "zach", "late", at(40)),
               lambda: ds.void_pick(c, did, 1, "zach", "late", at(40))):
        with pytest.raises(RuleError, match="already paid") as e:
            fn()
        assert isinstance(e.value, DraftError) and e.value.status == 409
    assert world.picks(did) == before and world.contract(x)[0] == a
    # a test draft in the same season is never blocked: it never pays
    tid = world.draft("paid-test", [[a, b]], clock=180, is_test=True, season=AWARD_SEASON)
    (t1,) = world.players(tid, 1)
    ds.make_pick(c, tid, a, t1, "web", "mgr-a", at(10))
    ds.undo_last_pick(c, tid, "zach", "test drafts can be corrected", at(20))


# -------------------------------------------------------------- 10: branch guard

def test_test_drafts_refused_off_a_test_branch(world, monkeypatch, capsys):
    c = world.conn
    a, b = world.teams[:2]
    monkeypatch.setattr(ds, "_db_host", lambda conn: "ep-cool-glade-123.us-east-2.aws.neon.tech")
    c.execute("delete from league_settings where key = 'branch'")
    try:
        with pytest.raises(DraftError, match="test database branch") as e:
            world.draft("branch", [[a, b]], start=False, is_test=True)
        assert e.value.status == 403 and c.execute("select count(*) from drafts where draft_id = 'dt-branch'").fetchone()[0] == 0
        c.execute("insert into league_settings (key, value) values ('branch', '\"main\"')")
        with pytest.raises(DraftError, match="test database branch"):
            world.draft("branch", [[a, b]], start=False, is_test=True)
        world.draft("branch-real", [[a, b]], start=False, is_test=False, season=AWARD_SEASON)      # a real draft is not this guard's business
        # the simulator refuses too, before it creates or deletes anything
        spec = importlib.util.spec_from_file_location("simulate", REPO_ROOT / "scripts" / "draft" / "simulate.py")
        sim = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sim)
        for flag in ("--setup", "--cleanup"):
            with pytest.raises(SystemExit, match="REFUSING"):
                sim.main(["--draft", "dt-sim-branch", flag, "--database-url", world.url])
        assert c.execute("select count(*) from drafts where draft_id = 'dt-sim-branch'").fetchone()[0] == 0
        c.execute("update league_settings set value = '\"test\"' where key = 'branch'")
        world.draft("branch", [[a, b]], start=False, is_test=True)                                 # branch = 'test': allowed
    finally:
        c.execute("delete from league_settings where key = 'branch'")


# -------------------------------------------------------------- 11: no spurious "Draft resumed" after a re-open

@pytest.mark.parametrize("how", ["undo", "void"])
def test_reopening_a_complete_draft_does_not_announce_a_resume(world, how):
    c = world.conn
    a, b = world.teams[:2]
    did = world.draft("reopen-" + how, [[a, b]], clock=30)       # a clock under 60 s, so a stale-runner extension would show
    x, y = world.players(did, 2)
    ds.make_pick(c, did, a, x, "web", "mgr-a", at(10))
    ds.make_pick(c, did, b, y, "web", "mgr-b", at(20))
    assert world.status(did) == "complete"
    mark = world.last_note()
    if how == "undo":
        ds.undo_last_pick(c, did, "zach", "misclick", at(100))
    else:
        ds.void_pick(c, did, 2, "zach", "wrong player", at(100))
    assert world.status(did) == "live"
    assert c.execute("select runner_heartbeat_at from draft_clock where draft_id = %s", (did,)).fetchone()[0] == at(100)
    assert world.tick(did, 101, stale=15) in (None, "warned")   # never "extended"
    assert world.notes("draft_resumed", after=mark) == []
    assert world.clock(did)[1] == at(130)                          # the full clock, not extended


# -------------------------------------------------------------- 12: schema rules

def test_schema_constraints(world):
    c = world.conn
    a, b = world.teams[:2]
    slots = world.slots("keeper", [[a, b], [a, b]])
    slots[1]["skip_reason"] = "keeper"                              # slot 2 is skipped
    did = "dt-schema"
    ds.create_draft(c, did, SEASON, "keeper", slots, is_test=True)
    x, y = world.players(did, 2)
    ins = f"insert into {PICKS} (draft_id, overall, upid, team_abbr, source, made_by, contract_tier) values (%s, %s, %s, %s, 'web', 'x', %s)"
    with pytest.raises(psycopg.errors.CheckViolation, match="skipped"):
        c.execute(ins, (did, 2, x, b, "VC-1"))                      # a pick cannot land in a skipped slot
    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="contract_tiers"):
        c.execute(ins, (did, 1, x, a, "NOPE-9"))                    # the tier must be a real one
    c.execute(ins, (did, 1, x, a, "VC-1"))
    # draft_clock: a running clock has no paused remainder, a paused one has no deadline
    ds.start(c, did, "zach", at(0), check=False)
    with pytest.raises(psycopg.errors.CheckViolation):
        c.execute("update draft_clock set paused_remaining_ms = 1000 where draft_id = %s", (did,))
    # one auto-pause per team per draft
    req = "insert into draft_add_requests (draft_id, requested_by, mlb_id, player_name, paused_draft) values (%s, %s, %s, 'n', %s)"
    c.execute(req, (did, a, 9990001, True))
    c.execute(req, (did, a, 9990002, False))
    c.execute(req, (did, b, 9990003, True))
    with pytest.raises(psycopg.errors.UniqueViolation):
        c.execute(req, (did, a, 9990004, True))
    assert c.execute("select data_type from information_schema.columns where table_name = 'notifications' and column_name = 'next_attempt_at'").fetchone()[0] == "timestamp with time zone"


def test_default_clocks_live_in_the_table_only(world):
    c = world.conn
    a, b = world.teams[:2]
    assert not hasattr(ds, "DEFAULT_CLOCK_SECONDS")
    assert dict((r[0], r[1:]) for r in c.execute("select draft_type, clock_seconds, forklift_seconds from draft_defaults")) == \
        {"prospect": (240, 10), "keeper": (180, 10)}
    for kind, secs in (("prospect", 240), ("keeper", 180)):
        did = f"dt-def-{kind}"
        ds.create_draft(c, did, SEASON, kind, world.slots(kind, [[a, b]]), is_test=True)
        assert c.execute("select clock_seconds, forklift_seconds from drafts where draft_id = %s", (did,)).fetchone() == (secs, 10)
    c.execute("update draft_defaults set clock_seconds = 200 where draft_type = 'keeper'")
    try:
        did = "dt-def-changed"
        ds.create_draft(c, did, SEASON, "keeper", world.slots("keeper", [[a, b]]), is_test=True)
        assert c.execute("select clock_seconds from drafts where draft_id = %s", (did,)).fetchone()[0] == 200
    finally:
        c.execute("update draft_defaults set clock_seconds = 180 where draft_type = 'keeper'")


# -------------------------------------------------------------- 13: importing the 2026 boards

def test_import_boards_from_app_state(world):
    c = world.conn
    real = [r[0] for r in c.execute("select upid from players order by upid::bigint limit 4")]
    key = "manager_boards_2097"
    a, b = world.teams[:2]
    board = {a: [real[0], "NOPE1", real[1], real[0], real[2]], b: [real[3]], "ZZZ": [real[0]]}
    c.execute("insert into app_state (key, value) values (%s, %s)", (key, json.dumps(board)))
    try:
        res = ds.import_boards_from_app_state(c, 2097)
        assert res["imported"] == {a: 3, b: 1}
        assert res["unknown_upids"] == {a: ["NOPE1"]} and res["unknown_teams"] == ["ZZZ"] and res["skipped_existing"] == []
        got = c.execute("select upid from draft_boards where season = 2097 and draft_type = 'prospect' and team_abbr = %s and slot_key = 'any' order by position", (a,)).fetchall()
        assert [r[0] for r in got] == [real[0], real[1], real[2]]
        again = ds.import_boards_from_app_state(c, 2097)                         # running twice changes nothing
        assert again["imported"] == {} and sorted(again["skipped_existing"]) == sorted([a, b])
        res = ds.import_boards_from_app_state(c, 2097, draft_type="keeper", target_season=2096)
        assert res["imported"] == {a: 3, b: 1}
        assert c.execute("select count(*) from draft_boards where season = 2096").fetchone()[0] == 4
        with pytest.raises(DraftError):
            ds.import_boards_from_app_state(c, 2095)
        # the real mirror imports too (all 12 teams; the 2026 data has no unknown players)
        real_res = ds.import_boards_from_app_state(c, 2026, target_season=2097, draft_type="keeper")
        assert len(real_res["imported"]) + len(real_res["skipped_existing"]) == 12
    finally:
        c.execute("delete from draft_boards where season in (2096, 2097)")
        c.execute("delete from app_state where key = %s", (key,))
