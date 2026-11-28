"""Two-way players (Constitution Art. V s03 Clause 1; Spec 07 "Two-way players"; migration 0009).

One contract held as two linked players rows, two roster spots, options, and the package rules.
Needs the local Postgres via DATABASE_URL; every test rolls back.
"""

import psycopg
import pytest
from psycopg.types.json import Jsonb

from league import actions as A
from league.actions import RuleError
from tests.league.test_actions import BY, S, bal, contract, fund, mk_keeper, mk_player, utc

BAT, PIT = "test-2w-bat", "test-2w-pit"


def mk_pair(conn, carried_tier=None):
    """A free-agent two-way player: a batter row and a pitcher row that name each other."""
    for upid, pos, twin in ((BAT, "DH", PIT), (PIT, "SP", BAT)):
        mk_player(conn, upid, positions=pos, carried_tier=carried_tier)
        conn.execute("update players set raw = %s where upid = %s", (Jsonb({"2wayplayer": twin}), upid))
    return BAT, PIT


def own_pair(conn, team="WIZ", tier="VC-2"):
    mk_pair(conn)
    for upid in (BAT, PIT):
        conn.execute("insert into contracts (upid, team_abbr, kind, tier, acquired_via) values (%s, %s, 'keeper', %s, 'import')",
                     (upid, team, tier))
        conn.execute("insert into roster_slots (upid, slot) values (%s, 'active')", (upid,))
    return BAT, PIT


def slot(conn, upid):
    r = conn.execute("select slot from roster_slots where upid = %s", (upid,)).fetchone()
    return r[0] if r else None


def commit_checks(conn):
    """Run the deferred constraints now, as a commit would (the tests never commit)."""
    conn.execute("set constraints all immediate")
    conn.execute("set constraints all deferred")


def fill_active(conn, team, relievers, outfielders):
    """Active keepers inside every positional maximum (the pair adds one batter and one SP)."""
    for i in range(relievers + outfielders):
        mk_keeper(conn, f"test-2w-fill-{i}", team, "TC-1")
        conn.execute("update players set positions = %s where upid = %s",
                     ("RP" if i < relievers else "OF", f"test-2w-fill-{i}"))


# ------------------------------------------------------------------ the link

def test_link_is_a_real_column_and_must_be_mutual(conn):
    mk_pair(conn)
    assert A._twin(conn, BAT) == PIT and A._twin(conn, PIT) == BAT
    commit_checks(conn)

    mk_player(conn, "test-2w-lonely")
    mk_player(conn, "test-2w-other")
    with pytest.raises(psycopg.errors.RaiseException, match="one-sided"), conn.transaction():
        conn.execute("update players set raw = %s where upid = 'test-2w-lonely'", (Jsonb({"2wayplayer": "test-2w-other"}),))
        conn.execute("set constraints all immediate")
    # and nobody can be the other half of two players
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        conn.execute("update players set raw = %s where upid = 'test-2w-lonely'", (Jsonb({"2wayplayer": BAT}),))
    # a twin that does not exist can't be committed either (the mutual check or the foreign key, whichever runs first)
    with pytest.raises((psycopg.errors.RaiseException, psycopg.errors.ForeignKeyViolation)), conn.transaction():
        conn.execute("update players set raw = %s where upid = 'test-2w-lonely'", (Jsonb({"2wayplayer": "no-such-upid"}),))
        conn.execute("set constraints all immediate")


# -------------------------------------------------------------- one contract

def test_owning_one_half_or_drifting_terms_cannot_be_committed(conn):
    mk_pair(conn)
    with pytest.raises(psycopg.errors.RaiseException, match="owned together"), conn.transaction():
        conn.execute("insert into contracts (upid, team_abbr, kind, tier, acquired_via) values (%s, 'WIZ', 'keeper', 'VC-2', 'import')", (BAT,))
        conn.execute("set constraints all immediate")

    for upid in (BAT, PIT):
        conn.execute("insert into contracts (upid, team_abbr, kind, tier, acquired_via) values (%s, 'WIZ', 'keeper', 'VC-2', 'import')", (upid,))
    commit_checks(conn)
    with pytest.raises(psycopg.errors.RaiseException, match="terms differ"), conn.transaction():
        conn.execute("update contracts set tier = 'FC-1' where upid = %s and valid_to is null", (PIT,))
        conn.execute("set constraints all immediate")


def test_keeper_draft_pick_gives_both_rows_and_the_undo_takes_both_back(conn):
    mk_pair(conn)
    r = A.assign_keeper_draft_pick(conn, PIT, "WIZ", 2, created_by=BY, season=S)
    assert r["tier"] == "VC-1"
    assert contract(conn, BAT)["tier"] == contract(conn, PIT)["tier"] == "VC-1"
    assert contract(conn, BAT)["team"] == "WIZ" and slot(conn, BAT) == slot(conn, PIT) == "active"
    commit_checks(conn)
    with pytest.raises(RuleError, match="already has an open contract"):
        A.assign_keeper_draft_pick(conn, BAT, "SAD", 3, created_by=BY, season=S)

    A.reverse_draft_pick(conn, PIT, "WIZ", created_by=BY, expected_tier="VC-1", season=S)
    assert contract(conn, BAT)["end_reason"] == contract(conn, PIT)["end_reason"] == "voided"
    assert slot(conn, BAT) is None and slot(conn, PIT) is None
    commit_checks(conn)


def test_kept_once_for_one_salary_and_both_rows_advance(conn):
    fund(conn, "WIZ", 300)
    own_pair(conn, tier="VC-2")
    r = A.keep_player(conn, BAT, created_by=BY, season=S)
    assert r["charged"] == 55 and bal(conn, "WIZ") == 245
    assert contract(conn, BAT)["tier"] == contract(conn, PIT)["tier"] == "FC-1"
    commit_checks(conn)
    # the other half is the same contract: no second salary
    for fn in (A.keep_player, A.reduce_a_tier):
        with pytest.raises(RuleError, match="already kept"):
            fn(conn, PIT, created_by=BY, season=S)
    assert bal(conn, "WIZ") == 245


def test_il_tag_applies_to_the_one_contract(conn):
    fund(conn, "WIZ", 300)
    own_pair(conn, tier="VC-2")
    r = A.il_tag(conn, PIT, created_by=BY, approved_by="zach", season=S)
    assert r["charged"] == 40                      # VC-2 $55 less the $15 VC discount, once
    b, p = contract(conn, BAT), contract(conn, PIT)
    assert b["il_tag"] and p["il_tag"] and b["tier"] == p["tier"] == "FC-1"
    commit_checks(conn)
    # one VC tag per team per season: the pair used exactly one
    other = mk_keeper(conn, "test-2w-vc", "WIZ", "VC-1")
    with pytest.raises(RuleError, match="already used its VC IL tag"):
        A.il_tag(conn, other, created_by=BY, approved_by="zach", season=S)
    assert bal(conn, "WIZ") == 300 - 40


def test_reduce_a_tier_applies_to_the_one_contract(conn):
    fund(conn, "WIZ", 300)
    own_pair(conn, tier="FC-1")
    r = A.reduce_a_tier(conn, BAT, created_by=BY, season=S)
    assert r["rat_price"] == 75 and r["salary"] == 55          # FC-1 reduced to VC-2, paid once
    b, p = contract(conn, BAT), contract(conn, PIT)
    assert b["rat_count"] == p["rat_count"] == 1 and b["tier"] == p["tier"] == "FC-1"
    commit_checks(conn)
    assert bal(conn, "WIZ") == 300 - 75 - 55


# ------------------------------------------------------------------- options

def test_each_half_takes_a_roster_spot_until_optioned(conn):
    own_pair(conn)
    fill_active(conn, "WIZ", 13, 11)               # 24 + both halves = 26
    extra = mk_player(conn, "test-2w-extra")
    with pytest.raises(RuleError, match="max 26"):
        A.acquire_free_agent(conn, extra, "WIZ", created_by=BY, season=S)

    r = A.option_two_way(conn, PIT, created_by=BY, season=S, now=utc(2027, 5, 1))
    assert slot(conn, PIT) == "optioned" and r["options_used"] == 1 and r["options_max"] == 2
    assert contract(conn, PIT)["valid_to"] is None            # still owned
    A.acquire_free_agent(conn, extra, "WIZ", created_by=BY, season=S)   # the spot is free now
    # recalling him needs the spot back
    with pytest.raises(RuleError, match="can't go active"):
        A.move_slot(conn, PIT, "active", created_by=BY, season=S)
    A.release(conn, extra, created_by=BY, season=S)
    assert A.move_slot(conn, PIT, "active", created_by=BY, season=S)["slot"] == "active"


def test_two_options_a_season_per_half_and_one_half_stays(conn):
    own_pair(conn)
    for day in (1, 10):
        A.option_two_way(conn, PIT, created_by=BY, season=S, now=utc(2027, 5, day))
        with pytest.raises(RuleError, match="has to stay on the roster"):
            A.option_two_way(conn, BAT, created_by=BY, season=S, now=utc(2027, 5, day))
        with pytest.raises(RuleError, match="already optioned"):
            A.option_two_way(conn, PIT, created_by=BY, season=S, now=utc(2027, 5, day))
        A.move_slot(conn, PIT, "active", created_by=BY, season=S)
    with pytest.raises(RuleError, match="no options left"):
        A.option_two_way(conn, PIT, created_by=BY, season=S, now=utc(2027, 5, 20))
    assert slot(conn, PIT) == "active"
    # the batter half has his own two, and a new season starts the count again
    conn.execute("""insert into roster_limits
                    select (jsonb_populate_record(null::roster_limits, to_jsonb(r) || '{"season": 2028}')).*
                      from roster_limits r where season = 2027""")
    assert A.option_two_way(conn, BAT, created_by=BY, season=S, now=utc(2027, 5, 21))["options_used"] == 1
    A.move_slot(conn, BAT, "active", created_by=BY, season=S)
    assert A.option_two_way(conn, PIT, created_by=BY, season=S + 1, now=utc(2028, 5, 1))["options_used"] == 1
    # the maximum is also a database rule, not just this function
    cid = conn.execute("select id from contracts where upid = %s and valid_to is null", (PIT,)).fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException, match="no two-way options left"), conn.transaction():
        conn.execute("insert into two_way_options (contract_id, upid, season, optioned_at, created_by) values (%s, %s, %s, now(), 'x')",
                     (cid, PIT, S))


def test_only_a_two_way_half_can_be_optioned(conn):
    plain = mk_keeper(conn, "test-2w-plain", "WIZ", "TC-1")
    with pytest.raises(RuleError, match="not a two-way player"):
        A.option_two_way(conn, plain, created_by=BY, season=S)
    with pytest.raises(RuleError, match="slot must be one of"):
        A.move_slot(conn, plain, "optioned", created_by=BY, season=S)
    with pytest.raises(psycopg.errors.RaiseException, match="only one half of a two-way player"), conn.transaction():
        conn.execute("update roster_slots set slot = 'optioned' where upid = %s", (plain,))


def test_a_trade_resets_the_options_for_the_new_team(conn):
    own_pair(conn)
    for day in (1, 10):
        A.option_two_way(conn, PIT, created_by=BY, season=S, now=utc(2027, 5, day))
        A.move_slot(conn, PIT, "active", created_by=BY, season=S)
    for upid in (BAT, PIT):
        A.transfer(conn, upid, "WIZ", "SAD", created_by=BY, season=S, now=utc(2027, 6, 1))
    commit_checks(conn)
    assert A.option_two_way(conn, PIT, created_by=BY, season=S, team="SAD", now=utc(2027, 6, 2))["options_used"] == 1


def test_trading_one_half_alone_cannot_be_committed(conn):
    own_pair(conn)
    with pytest.raises(psycopg.errors.RaiseException, match="team or terms differ"), conn.transaction():
        A.transfer(conn, BAT, "WIZ", "SAD", created_by=BY, season=S)
        conn.execute("set constraints all immediate")


# ------------------------------------------------------- release / free agent

def test_released_as_a_package(conn):
    own_pair(conn, tier="VC-2")
    r = A.release(conn, PIT, created_by=BY, season=S)
    assert set(r["released"]) == {BAT, PIT}
    assert contract(conn, BAT)["end_reason"] == contract(conn, PIT)["end_reason"] == "released"
    assert slot(conn, BAT) is None and slot(conn, PIT) is None
    rows = conn.execute("select carried_tier from players where upid = any(%s)", ([BAT, PIT],)).fetchall()
    assert rows == [("VC-2",), ("VC-2",)]
    commit_checks(conn)


def test_adding_one_half_as_a_free_agent_options_the_other(conn):
    mk_pair(conn, carried_tier="VC-2")
    r = A.acquire_free_agent(conn, BAT, "WIZ", created_by=BY, season=S, now=utc(2027, 5, 1))
    assert r["tier"] == "VC-2"
    assert slot(conn, BAT) == "active" and slot(conn, PIT) == "optioned"
    assert contract(conn, PIT)["team"] == "WIZ" and contract(conn, PIT)["tier"] == "VC-2"
    commit_checks(conn)
    # that used one of the pitcher half's two options
    A.move_slot(conn, PIT, "active", created_by=BY, season=S)
    assert A.option_two_way(conn, PIT, created_by=BY, season=S, now=utc(2027, 5, 9))["options_used"] == 2
    with pytest.raises(RuleError, match="owned"):
        A.acquire_free_agent(conn, PIT, "SAD", created_by=BY, season=S)


def test_adding_both_halves_needs_two_spots(conn):
    mk_pair(conn)
    fill_active(conn, "WIZ", 13, 12)               # 25 active: one spot left
    with pytest.raises(RuleError, match="can't go active"):
        A.acquire_free_agent(conn, BAT, "WIZ", created_by=BY, season=S, with_twin=True)
    assert contract(conn, BAT) is None and contract(conn, PIT) is None   # nothing half-done
    A.release(conn, "test-2w-fill-0", created_by=BY, season=S)
    A.acquire_free_agent(conn, BAT, "WIZ", created_by=BY, season=S, with_twin=True)
    assert slot(conn, BAT) == slot(conn, PIT) == "active"
    commit_checks(conn)
