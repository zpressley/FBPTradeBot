"""Reviewer fixes for Spec 07: locks on an unsaved day, MLB team aliases, generic pitchers, activate from
IL / NA only, scrubbing traded and released players out of saved lineups, locked rows for today.
Same helpers and rolled-back-transaction fixtures as test_service.py."""

from datetime import date

import pytest

from league import actions as A
from league.actions import RuleError
from lineups import service as L
from lineups.service import LineupConflict, LineupError
from tests.lineups.test_service import BY, MON, OTHER, TEAM, TUE, WED, at, fits, game, lineup, mk, save, stored


# ------------------------------------------------------------------ 1. lock bypass on a day with no stored lineup

def test_save_on_an_unsaved_day_locks_started_players_and_returns_a_committed_conflict(conn):
    early, late, other = mk(conn, "OF", mlb_team="NYM"), mk(conn, "OF", mlb_team="BOS"), mk(conn, "OF", mlb_team="MIA")
    game(conn, 9_100_001, TUE, at(TUE, 13, 5), "NYM")
    save(conn, TEAM, MON, lineup(("OF", 1, early), ("OF", 2, late)), at(MON, 9))     # Tuesday has no rows
    assert conn.execute("select count(*) from lineups where team_abbr = %s and game_date = %s", (TEAM, TUE)).fetchone()[0] == 0
    now = at(TUE, 13, 10)
    with pytest.raises(LineupConflict) as e:       # the lock is a change the caller (version 0) didn't see
        L.save_lineup(conn, TEAM, TUE, lineup(("OF", 1, other), ("OF", 2, late)), 0, "manager", now)
    latest = e.value.latest
    # the version the client is told about exists in the database, and the lock is stored
    assert conn.execute("select version from lineup_versions where team_abbr = %s and game_date = %s",
                        (TEAM, TUE)).fetchone()[0] == latest["version"] >= 1
    assert next(s for s in latest["slots"] if s["upid"] == early)["locked_at"] is not None
    assert all(s["set_by"] == "carryover" for s in latest["slots"] if s["upid"])
    # with the latest version the started player still can't be moved or replaced
    with pytest.raises(LineupError, match="locked"):
        L.save_lineup(conn, TEAM, TUE, lineup(("OF", 1, other), ("OF", 2, late)), latest["version"], "manager", now)
    L.save_lineup(conn, TEAM, TUE, lineup(("OF", 1, early), ("UTIL", 1, late), ("OF", 2, other)), latest["version"],
                  "manager", now)
    assert stored(conn, TEAM, TUE) == {("OF", 1): early, ("UTIL", 1): late, ("OF", 2): other}


def test_first_save_on_an_untouched_day_with_nothing_started_does_not_conflict(conn):
    a, b = mk(conn, "C"), mk(conn, "1B")
    save(conn, TEAM, MON, lineup(("C", 1, a)), at(MON, 9))
    L.save_lineup(conn, TEAM, TUE, lineup(("C", 1, a), ("1B", 1, b)), 0, "manager", at(MON, 10))   # version 0 = the copy he saw
    assert stored(conn, TEAM, TUE) == {("C", 1): a, ("1B", 1): b}


def test_a_player_whose_game_started_cannot_be_placed_in_a_starting_slot(conn):
    gone = mk(conn, "OF", mlb_team="NYM")
    game(conn, 9_100_002, MON, at(MON, 13, 5), "NYM")
    now = at(MON, 13, 10)
    with pytest.raises(LineupError, match="game has started"):
        L.save_lineup(conn, TEAM, MON, lineup(("OF", 1, gone)), 0, "manager", now)
    assert stored(conn, TEAM, MON) == {}
    # the bench is still fine
    L.save_lineup(conn, TEAM, MON, lineup(("BN", 1, gone)), 0, "manager", now)
    assert stored(conn, TEAM, MON) == {("BN", 1): gone}


def test_future_days_come_from_league_settings(conn):
    a = mk(conn, "C")
    day = date(2027, 4, 13)       # 8 days after Monday 4/5
    with pytest.raises(LineupError, match="up to 7 days"):
        save(conn, TEAM, day, lineup(("C", 1, a)), at(MON, 9))
    conn.execute("update league_settings set value = '9' where key = 'lineup_future_days'")
    save(conn, TEAM, day, lineup(("C", 1, a)), at(MON, 9))
    assert stored(conn, TEAM, day) == {("C", 1): a}


# ------------------------------------------------------------------ 2. MLB team abbreviation aliases

def test_aliased_team_abbreviations_lock_and_count_as_a_game(conn):
    oak = mk(conn, "OF", mlb_team="OAK")
    game(conn, 9_100_003, TUE, at(TUE, 13, 5), "ATH", away="WSN")
    wsn = mk(conn, "OF", mlb_team="WSH")
    save(conn, TEAM, TUE, lineup(("OF", 1, oak), ("OF", 2, wsn)), at(MON, 9))
    assert L.lock_started_games(conn, at(TUE, 13, 10)) == 1
    locked = {s["upid"] for s in L.get_lineup(conn, TEAM, TUE)["slots"] if s["locked_at"]}
    assert locked == {oak, wsn}                                    # OAK = ATH, WSN = WSH


def test_optimize_sees_a_game_for_an_aliased_team(conn):
    game(conn, 9_100_004, TUE, at(TUE, 19, 5), "ATH")
    plays = mk(conn, "C", mlb_team="OAK", rank=300)
    idle = mk(conn, "C", mlb_team="MIA", rank=1)
    got = {r["upid"]: r["slot_code"] for r in L.optimize(conn, TEAM, TUE) if r["upid"]}
    assert got[plays] == "C" and got[idle] != "C"


def test_alias_table_has_the_thirty_teams_and_the_renames(conn):
    n = conn.execute("select count(distinct abbr) from mlb_team_aliases").fetchone()[0]
    assert n == 30
    for alias, abbr in (("OAK", "ATH"), ("ARI", "AZ"), ("WSN", "WSH"), ("TBR", "TB"), ("KCR", "KC"), ("SDP", "SD"),
                        ("SFG", "SF"), ("CHW", "CWS"), ("LA", "LAD"), ("NYM", "NYM")):
        assert conn.execute("select mlb_abbr(%s)", (alias,)).fetchone()[0] == abbr
    assert conn.execute("select mlb_abbr('ZZZ')").fetchone()[0] == "ZZZ"


# ------------------------------------------------------------------ 3. generic pitcher positions

def test_a_generic_p_rookie_with_no_stats_gets_sp_and_rp(conn):
    rookie = mk(conn, "P", elig=[])
    L.refresh_eligibility(conn, 2027, [], at(MON, 3))
    rows = conn.execute("select position, source from position_eligibility where upid = %s order by 1", (rookie,)).fetchall()
    assert rows == [("RP", "primary"), ("SP", "primary")]
    assert all(fits(conn, rookie, c) for c in ("SP", "RP", "P", "BN")) and not fits(conn, rookie, "C")


@pytest.mark.parametrize("pos", ["P", "RHP", "LHP"])
def test_a_generic_pitcher_gets_sp_when_starts_beat_relief_else_rp(conn, pos):
    starter = mk(conn, pos, elig=[], mlb_id=9_900_201)
    reliever = mk(conn, pos, elig=[], mlb_id=9_900_202)
    L.refresh_eligibility(conn, 2027, [
        {"mlb_id": 9_900_201, "position": "P", "games": 4, "starts": 3, "relief": 1},
        {"mlb_id": 9_900_202, "position": "P", "games": 4, "starts": 1, "relief": 3}], at(MON, 3), source="last_season")
    got = lambda u: {r[0] for r in conn.execute("select position from position_eligibility where upid = %s", (u,))}  # noqa: E731
    assert got(starter) == {"SP"} and got(reliever) == {"RP"}


# ------------------------------------------------------------------ 5. activate only from IL / NA

def test_activate_only_from_il_or_na(conn):
    farm, active, il, na = (mk(conn, "OF", slot=s) for s in ("farm", "active", "il", "na"))
    for u in (farm, active):
        with pytest.raises(LineupError, match="only IL and NA"):
            L.activate(conn, TEAM, u, BY, at(MON, 9))
    with pytest.raises(LineupError, match="graduate"):
        L.activate(conn, TEAM, farm, BY, at(MON, 9))
    assert conn.execute("select slot from roster_slots where upid = %s", (farm,)).fetchone()[0] == "farm"
    L.activate(conn, TEAM, il, BY, at(MON, 9))
    L.activate(conn, TEAM, na, BY, at(MON, 9))
    assert {conn.execute("select slot from roster_slots where upid = %s", (u,)).fetchone()[0] for u in (il, na)} == {"active"}


def test_il_limit_comes_from_roster_limits_through_the_lineup_wrapper(conn):
    for _ in range(5):
        mk(conn, "OF", slot="il")
    sixth = mk(conn, "OF")
    conn.execute("insert into mlb_roster_status (upid, status, updated_at) values (%s, 'il', now())", (sixth,))
    with pytest.raises(RuleError, match="5 on IL"):
        L.move_to_il(conn, TEAM, sixth, BY, at(MON, 9))


# ------------------------------------------------------------------ 6, 7. stale players in saved lineups

def test_transfer_removes_the_player_from_the_old_teams_saved_future_lineups(conn):
    a, b = mk(conn, "C"), mk(conn, "1B")
    for day in (MON, TUE, WED):
        save(conn, TEAM, day, lineup(("C", 1, a), ("1B", 1, b)), at(MON, 9))
    v = L.get_lineup(conn, TEAM, WED)["version"]
    A.transfer(conn, a, TEAM, OTHER, created_by=BY, now=at(MON, 12))
    for day in (MON, TUE, WED):
        assert stored(conn, TEAM, day) == {("1B", 1): b}
    assert L.get_lineup(conn, TEAM, WED)["version"] == v + 1


def test_release_move_out_of_active_and_graduate_scrub_the_lineups(conn):
    a, b, c = mk(conn, "C"), mk(conn, "1B"), mk(conn, "2B")
    save(conn, TEAM, TUE, lineup(("C", 1, a), ("1B", 1, b), ("2B", 1, c)), at(MON, 9))
    A.release(conn, a, created_by=BY, team=TEAM, now=at(MON, 12))
    A.move_slot(conn, b, "il", created_by=BY, team=TEAM, now=at(MON, 12))
    assert stored(conn, TEAM, TUE) == {("2B", 1): c}
    # graduate goes through the same hook (no harm when the prospect was never in a lineup)
    calls = []
    A.on_roster_change(lambda conn_, team, upids, now: calls.append((team, upids)))
    try:
        A.move_slot(conn, c, "na", created_by=BY, team=TEAM, now=at(MON, 12))
    finally:
        A._ROSTER_HOOKS.pop()
    assert calls == [(TEAM, [c])]
    assert stored(conn, TEAM, TUE) == {}


def test_scrub_leaves_a_locked_row_for_today_but_clears_tomorrow(conn):
    p, q = mk(conn, "OF", mlb_team="NYM"), mk(conn, "1B", mlb_team="MIA")
    game(conn, 9_100_005, MON, at(MON, 13, 5), "NYM")
    save(conn, TEAM, MON, lineup(("OF", 1, p), ("1B", 1, q)), at(MON, 9))
    save(conn, TEAM, TUE, lineup(("OF", 1, p), ("1B", 1, q)), at(MON, 9))
    now = at(MON, 13, 10)
    L.lock_started_games(conn, now)
    L.scrub_lineups(conn, TEAM, [p, q], now)
    assert stored(conn, TEAM, MON) == {("OF", 1): p}          # p is locked today and stays; q (no game yet) is gone
    assert L.get_lineup(conn, TEAM, MON)["slots"][6]["locked_at"] is not None
    assert stored(conn, TEAM, TUE) == {}
    L.scrub_lineups(conn, TEAM, [p, q], now)                  # twice is fine
    assert stored(conn, TEAM, MON) == {("OF", 1): p}


def test_drop_from_lineups_keeps_todays_locked_row(conn):
    p = mk(conn, "OF", mlb_team="NYM")
    game(conn, 9_100_006, MON, at(MON, 13, 5), "NYM")
    save(conn, TEAM, MON, lineup(("OF", 1, p)), at(MON, 9))
    save(conn, TEAM, TUE, lineup(("OF", 1, p)), at(MON, 9))
    L.lock_started_games(conn, at(MON, 13, 10))
    L._drop_from_lineups(conn, TEAM, [p], at(MON, 13, 10))
    assert stored(conn, TEAM, MON) == {("OF", 1): p} and stored(conn, TEAM, TUE) == {}


# ------------------------------------------------------------------ 8. optimize tiebreak

def test_optimize_breaks_rank_ties_by_upid(conn):
    game(conn, 9_100_007, MON, at(MON, 19, 5), "NYM")
    hi, lo = mk(conn, "C", rank=50, upid="test-lu-tie-b"), mk(conn, "C", rank=50, upid="test-lu-tie-a")
    first = L.optimize(conn, TEAM, MON)
    assert {r["upid"]: r["slot_code"] for r in first if r["upid"]}[lo] == "C"       # lower upid wins the tie
    assert L.optimize(conn, TEAM, MON) == first
