"""Spec 07 tests 1-11 (specs/07_team_page_lineups.md, "Tests"), plus a few for the pieces around them.

Each test runs in a rolled-back transaction on throwaway 'test-...' players with contracts and
roster_slots rows inserted directly (the cutover has not filled those tables yet).
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from psycopg.types.json import Jsonb

from league import actions as A
from league.actions import RuleError
from lineups import service as L
from lineups.service import LineupConflict, LineupError

ET = ZoneInfo("America/New_York")
MON, TUE, WED = date(2027, 4, 5), date(2027, 4, 6), date(2027, 4, 7)
TEAM, OTHER = "WAR", "WIZ"
BY = "test"
_n = [0]


def at(day, h, m=0):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=ET)


def mk(conn, positions, *, team=TEAM, slot="active", mlb_team="NYM", rank=None, elig=None, mlb_id=None,
       primary=None, raw=None, upid=None, name=None):
    """A throwaway player with an open contract and a roster slot. `elig` = list of eligibility positions."""
    _n[0] += 1
    upid = upid or f"test-lu-{_n[0]}"
    conn.execute("""insert into players (upid, name, positions, mlb_primary_position, mlb_team, rank, mlb_id, raw)
                    values (%s, %s, %s, %s, %s, %s, %s, %s)""",
                 (upid, name or f"Test {upid}", positions, primary or positions.split(",")[0], mlb_team, rank, mlb_id,
                  Jsonb(raw) if raw else None))
    conn.execute("insert into contracts (upid, team_abbr, kind, tier, acquired_via) values (%s, %s, 'keeper', 'TC-1', 'import')",
                 (upid, team))
    conn.execute("insert into roster_slots (upid, slot) values (%s, %s)", (upid, slot))
    for pos in (elig if elig is not None else positions.replace(" ", "").split(",")):
        conn.execute("insert into position_eligibility (upid, season, position, source) values (%s, 2027, %s, 'primary')",
                     (upid, pos))
    return upid


def lineup(*cells):
    """lineup(("C", 1, upid), ("OF", 2, upid))"""
    return [{"slot_code": c, "slot_index": i, "upid": u} for c, i, u in cells]


def save(conn, team, day, cells, now, by="manager"):
    v = L.get_lineup(conn, team, day)["version"]
    return L.save_lineup(conn, team, day, cells, v, by, now)


def stored(conn, team, day):
    return {(s["slot_code"], s["slot_index"]): s["upid"] for s in L.get_lineup(conn, team, day)["slots"] if s["upid"]}


def fits(conn, upid, code, day=MON):
    try:
        L.validate(conn, TEAM, day, lineup((code, 1, upid)))
        return True
    except LineupError:
        return False


def game(conn, pk, day, start, home, away="ZZZ", status="scheduled"):
    L.refresh_games(conn, [{"game_pk": pk, "game_date": day, "start_time": start, "home_team": home,
                            "away_team": away, "status": status}], start)


# ------------------------------------------------------------------ 1, 2

def test_1_second_base_shortstop_fits_2b_ss_util_bn_only(conn):
    u = mk(conn, "2B,SS")
    ok = {c for c in ("C", "1B", "2B", "SS", "3B", "CF", "OF", "UTIL", "SP", "RP", "P", "BN") if fits(conn, u, c)}
    assert ok == {"2B", "SS", "UTIL", "BN"}


def test_2_cf_fills_cf_or_either_of_slot_and_of_only_cannot_fill_cf(conn):
    cf = mk(conn, "CF", elig=["CF"])           # CF-eligible; OF comes through the slot's accepts list
    of = mk(conn, "OF")
    L.validate(conn, TEAM, MON, lineup(("CF", 1, cf)))
    L.validate(conn, TEAM, MON, lineup(("OF", 1, cf)))
    L.validate(conn, TEAM, MON, lineup(("OF", 2, cf)))
    L.validate(conn, TEAM, MON, lineup(("OF", 1, of)))
    L.validate(conn, TEAM, MON, lineup(("OF", 2, of)))
    with pytest.raises(LineupError, match="can't play CF"):
        L.validate(conn, TEAM, MON, lineup(("CF", 1, of)))


# ------------------------------------------------------------------ 3

def test_3_fifth_game_tuesday_means_eligible_wednesday(conn):
    p = mk(conn, "2B", mlb_id=9_900_001)
    four = mk(conn, "2B", mlb_id=9_900_002)
    rows = [{"mlb_id": 9_900_001, "position": "3B", "games": 5, "starts": 5, "relief": 0},
            {"mlb_id": 9_900_002, "position": "3B", "games": 4, "starts": 4, "relief": 0}]
    L.refresh_eligibility(conn, 2027, rows, at(WED, 3), through=TUE)       # stats through Tuesday's games
    assert not fits(conn, p, "3B", TUE)
    assert fits(conn, p, "3B", WED)
    assert not fits(conn, four, "3B", WED)
    # never lost: a later refresh with fewer games (a stat correction) removes nothing
    L.refresh_eligibility(conn, 2027, [dict(rows[0], games=3)], at(WED, 4), through=WED)
    assert fits(conn, p, "3B", WED)


def test_3b_last_season_games_count_from_day_one_and_pitchers_use_starts_and_relief(conn):
    sp = mk(conn, "RP", mlb_id=9_900_003)
    L.refresh_eligibility(conn, 2027, [{"mlb_id": 9_900_003, "position": "P", "games": 40, "starts": 6, "relief": 34}],
                          at(MON, 3), source="last_season")
    assert fits(conn, sp, "SP") and fits(conn, sp, "RP")


# ------------------------------------------------------------------ 4

def test_4_carryover_copies_monday_to_tuesday_and_drops_a_player_moved_to_il(conn):
    a, b, c = mk(conn, "C"), mk(conn, "1B"), mk(conn, "2B")
    save(conn, TEAM, MON, lineup(("C", 1, a), ("1B", 1, b), ("2B", 1, c)), at(MON, 9))
    A.move_slot(conn, b, "il", created_by=BY, team=TEAM, now=at(MON, 22))
    L.carryover(conn, TUE, at(TUE, 0))
    got = stored(conn, TEAM, TUE)
    assert got == {("C", 1): a, ("2B", 1): c}
    assert all(s["set_by"] == "carryover" for s in L.get_lineup(conn, TEAM, TUE)["slots"])


def test_4b_carryover_never_overwrites_an_edited_day_and_runs_twice_safely(conn):
    a, b = mk(conn, "C"), mk(conn, "1B")
    save(conn, TEAM, MON, lineup(("C", 1, a)), at(MON, 9))
    save(conn, TEAM, TUE, lineup(("1B", 1, b)), at(MON, 10))        # a manager set Tuesday ahead of time
    L.carryover(conn, TUE, at(TUE, 0))
    L.carryover(conn, WED, at(WED, 0))
    L.carryover(conn, WED, at(WED, 0))
    assert stored(conn, TEAM, TUE) == {("1B", 1): b}
    assert stored(conn, TEAM, WED) == {("1B", 1): b}                # copied from Tuesday, the latest earlier day


# ------------------------------------------------------------------ 5, 6

def test_5_started_player_is_locked_and_a_later_game_is_not(conn):
    early, late = mk(conn, "OF", mlb_team="NYM"), mk(conn, "OF", mlb_team="BOS")
    game(conn, 9_000_001, MON, at(MON, 13, 5), "NYM")
    game(conn, 9_000_002, MON, at(MON, 19, 5), "BOS")
    save(conn, TEAM, MON, lineup(("OF", 1, early), ("OF", 2, late)), at(MON, 9))
    now = at(MON, 13, 10)
    L.lock_started_games(conn, now)
    v = L.get_lineup(conn, TEAM, MON)["version"]
    with pytest.raises(LineupError, match="locked"):
        L.save_lineup(conn, TEAM, MON, lineup(("UTIL", 1, early), ("OF", 2, late)), v, "manager", now)
    # nobody can move into his slot either
    other = mk(conn, "OF")
    with pytest.raises(LineupError, match="locked"):
        L.save_lineup(conn, TEAM, MON, lineup(("OF", 1, other), ("OF", 2, late)), v, "manager", now)
    L.save_lineup(conn, TEAM, MON, lineup(("OF", 1, early), ("UTIL", 1, late)), v, "manager", now)
    assert stored(conn, TEAM, MON) == {("OF", 1): early, ("UTIL", 1): late}


def test_5b_save_locks_a_game_that_started_before_the_job_ran(conn):
    early = mk(conn, "OF", mlb_team="NYM")
    game(conn, 9_000_003, MON, at(MON, 13, 5), "NYM")
    save(conn, TEAM, MON, lineup(("OF", 1, early)), at(MON, 9))
    v = L.get_lineup(conn, TEAM, MON)["version"]
    with pytest.raises(LineupConflict):     # the lock kicking in changes the lineup under the caller
        L.save_lineup(conn, TEAM, MON, lineup(("UTIL", 1, early)), v, "manager", at(MON, 13, 10))


def test_6_postponed_game_unlocks_the_slot(conn):
    p = mk(conn, "OF", mlb_team="NYM")
    game(conn, 9_000_004, MON, at(MON, 13, 5), "NYM")
    save(conn, TEAM, MON, lineup(("OF", 1, p)), at(MON, 9))
    L.lock_started_games(conn, at(MON, 13, 10))
    assert L.get_lineup(conn, TEAM, MON)["slots"][6]["locked_at"] is not None
    game(conn, 9_000_004, MON, at(MON, 13, 5), "NYM", status="postponed")
    v = L.get_lineup(conn, TEAM, MON)["version"]
    L.save_lineup(conn, TEAM, MON, lineup(("UTIL", 1, p)), v, "manager", at(MON, 13, 20))
    assert stored(conn, TEAM, MON) == {("UTIL", 1): p}


# ------------------------------------------------------------------ 7

def test_7_second_device_gets_lineup_changed_and_nothing_is_lost(conn):
    a, b = mk(conn, "C"), mk(conn, "C")
    phone, laptop = L.get_lineup(conn, TEAM, TUE), L.get_lineup(conn, TEAM, TUE)
    L.save_lineup(conn, TEAM, TUE, lineup(("C", 1, a)), phone["version"], "manager", at(MON, 20))
    with pytest.raises(LineupConflict, match="Lineup changed") as e:
        L.save_lineup(conn, TEAM, TUE, lineup(("C", 1, b)), laptop["version"], "manager", at(MON, 20).replace(second=1))
    assert {s["upid"] for s in e.value.latest["slots"] if s["upid"]} == {a}
    assert e.value.latest["version"] == phone["version"] + 1
    assert stored(conn, TEAM, TUE) == {("C", 1): a}


# ------------------------------------------------------------------ 8

def test_8_optimize_fills_the_nine_batting_slots_with_players_who_have_games(conn):
    game(conn, 9_000_005, MON, at(MON, 19, 5), "NYM")
    # 8 batters with a game (NYM); g7 (2B,SS) must take 2B so that g8 (SS only) can play SS
    g = {name: mk(conn, pos, mlb_team="NYM", rank=50 + i, elig=elig)
         for i, (name, pos, elig) in enumerate([
             ("c", "C", ["C"]), ("1b", "1B", ["1B"]), ("3b", "3B", ["3B"]), ("cf", "CF", ["CF"]),
             ("of1", "OF", ["OF"]), ("of2", "OF", ["OF"]), ("ss2b", "SS", ["2B", "SS"]), ("ss", "SS", ["SS"])])}
    # 4 batters with no game (MIA), better ranked than everyone
    n = [mk(conn, pos, mlb_team="MIA", rank=1 + i) for i, pos in enumerate(["2B", "C", "1B", "OF"])]
    got = {(r["slot_code"], r["slot_index"]): r["upid"] for r in L.optimize(conn, TEAM, MON) if r["upid"]}
    batting = [("C", 1), ("1B", 1), ("2B", 1), ("SS", 1), ("3B", 1), ("CF", 1), ("OF", 1), ("OF", 2), ("UTIL", 1)]
    assert all(k in got for k in batting)
    starters = [got[k] for k in batting]
    assert set(g.values()) <= set(starters)                  # all 8 with a game start
    assert got[("2B", 1)] == g["ss2b"] and got[("SS", 1)] == g["ss"]
    assert got[("UTIL", 1)] == n[0]                          # the 9th slot: best-ranked of those with no game
    assert len([v for k, v in got.items() if k[0] == "BN"]) == 3
    # the proposal is valid as it stands
    L.validate(conn, TEAM, MON, L.optimize(conn, TEAM, MON))


def test_8b_optimize_prefers_the_better_ranked_player_when_both_have_games(conn):
    game(conn, 9_000_006, MON, at(MON, 19, 5), "NYM")
    worse, better = mk(conn, "C", rank=300), mk(conn, "C", rank=10)
    assert {r["upid"]: r["slot_code"] for r in L.optimize(conn, TEAM, MON) if r["upid"]}[better] == "C"


# ------------------------------------------------------------------ 9

def test_9_two_way_batter_cannot_fill_a_p_slot_and_both_rows_count(conn):
    bat = mk(conn, "Util", primary="TWP", mlb_id=9_900_010, upid="test-lu-oh-bat", raw={"2wayplayer": "test-lu-oh-pit"},
             elig=[])
    pit = mk(conn, "SP", primary="TWP", mlb_id=9_900_010, upid="test-lu-oh-pit", raw={"2wayplayer": "test-lu-oh-bat"},
             elig=[])
    L.refresh_eligibility(conn, 2027, [{"mlb_id": 9_900_010, "position": "P", "games": 10, "starts": 10, "relief": 0}],
                          at(MON, 3), source="last_season")
    assert fits(conn, bat, "UTIL") and fits(conn, pit, "P") and fits(conn, pit, "SP")
    assert not fits(conn, bat, "P") and not fits(conn, bat, "SP")
    assert not fits(conn, pit, "UTIL")
    with pytest.raises(LineupError, match="batter"):
        L.admin_add_eligibility(conn, bat, 2027, "SP", "test", "zach")
    both = {p["upid"] for p in A._active_players(conn, TEAM)}
    assert {bat, pit} <= both                                 # two roster spots
    lim = {"active_max": 1, "sp_max": 8, "sp_max_with_swing": 9, "pitcher_max": 14, "batter_max": 16, "catcher_min": 0}
    assert [v["rule"] for v in A.roster_violations(A._active_players(conn, TEAM), lim)] == ["active_max"]
    out = L.release(conn, TEAM, bat, BY, at(MON, 9))          # released as a package
    assert set(out["released"]) == {bat, pit}


# ------------------------------------------------------------------ 10, 11

def test_10_activating_from_il_with_26_active_is_blocked_with_the_reason(conn):
    for _ in range(26):
        mk(conn, "OF")
    il = mk(conn, "OF", slot="il")
    with pytest.raises(RuleError, match="max 26"):
        L.activate(conn, TEAM, il, BY, at(MON, 9))
    assert conn.execute("select slot from roster_slots where upid = %s", (il,)).fetchone()[0] == "il"


def test_11_il_player_in_a_starting_slot_is_rejected(conn):
    il = mk(conn, "OF", slot="il")
    with pytest.raises(LineupError, match="IL"):
        L.save_lineup(conn, TEAM, MON, lineup(("OF", 1, il)), 0, "manager", at(MON, 9))
    assert stored(conn, TEAM, MON) == {}


# ------------------------------------------------------------------ the rest of validate and the moves

def test_validate_rejects_duplicates_other_teams_and_bad_slots(conn):
    a, theirs = mk(conn, "OF"), mk(conn, "OF", team=OTHER)
    with pytest.raises(LineupError) as e:
        L.validate(conn, TEAM, MON, lineup(("OF", 1, a), ("OF", 2, a), ("UTIL", 1, theirs), ("OF", 3, a)))
    text = " ".join(e.value.problems)
    assert "more than one slot" in text and "not on WAR" in text and "no OF slot 3" in text


def test_save_only_from_today_to_seven_days_ahead(conn):
    with pytest.raises(LineupError, match="7 days"):
        L.save_lineup(conn, TEAM, date(2027, 4, 13), [], 0, "manager", at(MON, 9))
    with pytest.raises(LineupError, match="7 days"):
        L.save_lineup(conn, TEAM, date(2027, 4, 4), [], 0, "manager", at(MON, 9))
    L.save_lineup(conn, TEAM, date(2027, 4, 12), [], 0, "manager", at(MON, 9))


def test_move_to_il_and_na_need_mlb_status_and_clear_the_lineup(conn):
    p, q = mk(conn, "OF"), mk(conn, "OF")
    save(conn, TEAM, TUE, lineup(("OF", 1, p), ("OF", 2, q)), at(MON, 9))
    with pytest.raises(LineupError, match="IL"):
        L.move_to_il(conn, TEAM, p, BY, at(MON, 10))
    L.refresh_mlb_status(conn, [], at(MON, 9))
    conn.execute("update players set mlb_id = 9900020 where upid = %s", (p,))
    conn.execute("update players set mlb_id = 9900021 where upid = %s", (q,))
    L.refresh_mlb_status(conn, [{"mlb_id": 9900020, "status": "il", "status_code": "D15", "primary_position": "OF"},
                                {"mlb_id": 9900021, "status": "active", "status_code": "A", "primary_position": "OF"}],
                         at(MON, 9))
    with pytest.raises(LineupError, match="NA"):
        L.move_to_na(conn, TEAM, q, BY, at(MON, 10))
    L.move_to_il(conn, TEAM, p, BY, at(MON, 10))
    assert stored(conn, TEAM, TUE) == {("OF", 2): q}
    assert L.get_lineup(conn, TEAM, TUE)["version"] == 2
    L.activate(conn, TEAM, p, BY, at(MON, 11))


def test_admin_add_eligibility_needs_a_reason(conn):
    p = mk(conn, "1B")
    with pytest.raises(LineupError, match="reason"):
        L.admin_add_eligibility(conn, p, 2027, "C", " ", "zach")
    L.admin_add_eligibility(conn, p, 2027, "C", "caught 3 games in spring", "zach")
    assert fits(conn, p, "C")
    assert conn.execute("select source from position_eligibility where upid = %s and position = 'C'", (p,)).fetchone()[0] == "admin"
