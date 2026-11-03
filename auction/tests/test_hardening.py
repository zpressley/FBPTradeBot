"""Review fixes A1-A8 for auction/service.py and the 0002/0006 schema backstops.

Same setup as test_service.py (local Postgres, throwaway 'auction-test-*' players, real commits
cleaned up in `finally`). Tests that only need rolled-back changes run inside a transaction that is
rolled back at the end.
"""

import psycopg
import pytest

from auction import rules, service
from auction.rules import BidError
from auction.tests.test_service import (  # noqa: F401  (fixtures world and db_url are used by name)
    BY, CAL, MON_4PM, SUN_NOON, THU_NOON, WED_NOON, WEEK, at, connect, db_url, world,
)
from league import actions

ADMIN = "WIZ"
SAT_NOON = at(5, 12)


class Rollback(Exception):
    pass


def in_rollback(conn, fn):
    """Run fn() inside a transaction that is always rolled back; return fn's result."""
    out = []
    with pytest.raises(Rollback):
        with conn.transaction():
            out.append(fn())
            raise Rollback()
    return out[0]


def plain_team(world):
    return next(t for t in world.active if t != ADMIN and not world.conn.execute(
        "select role = 'admin' from teams where abbr = %s", (t,)).fetchone()[0])


def open_week(world, a, b, amount=10):
    """a holds an OB on prospect 1, b an OB on prospect 2; both funded."""
    world.fund(a, 100)
    world.fund(b, 100)
    src = world.source()
    service.place_bid(world.conn, a, world.upids[0], "OB", amount, "web", MON_4PM, CAL, src)
    service.place_bid(world.conn, b, world.upids[1], "OB", amount, "web", MON_4PM, CAL, src)


# ---------------------------------------------------------------- A1

def test_a1_prices_come_from_wb_prices(world):
    a = world.teams[0]
    world.fund(a, 100)

    def raised_min_ob():
        world.conn.execute("update wb_prices set amount = 20 where season = 2027 and item = 'auction_min_ob'")
        with pytest.raises(BidError, match=r"at least \$20"):
            service.place_bid(world.conn, a, world.upids[0], "OB", 15, "web", MON_4PM, CAL, world.source())
        service.place_bid(world.conn, a, world.upids[0], "OB", 20, "web", MON_4PM, CAL, world.source())
    in_rollback(world.conn, raised_min_ob)

    def raised_min_raise():
        service.place_bid(world.conn, a, world.upids[0], "OB", 10, "web", MON_4PM, CAL, world.source())
        world.fund(world.teams[1], 100)
        world.conn.execute("update wb_prices set amount = 8 where season = 2027 and item = 'auction_min_raise'")
        with pytest.raises(BidError, match="at least \\$8 above"):
            service.place_bid(world.conn, world.teams[1], world.upids[0], "CB", 15, "web", WED_NOON, CAL)
        service.place_bid(world.conn, world.teams[1], world.upids[0], "CB", 18, "web", WED_NOON, CAL)
    in_rollback(world.conn, raised_min_raise)


@pytest.mark.parametrize("item", ["auction_min_ob", "auction_min_raise"])
def test_a1_missing_price_row_is_a_clear_error(world, item):
    a = world.teams[0]
    world.fund(a, 100)

    def go():
        world.conn.execute("delete from wb_prices where season = 2027 and item = %s", (item,))
        with pytest.raises(BidError, match=item):
            service.place_bid(world.conn, a, world.upids[0], "OB", 10, "web", MON_4PM, CAL, world.source())
    in_rollback(world.conn, go)


# ---------------------------------------------------------------- A2

def test_a2_prospect_owned_after_bidding_is_unsold_not_a_blocker(world):
    a, b, c = world.teams[:3]
    open_week(world, a, b)
    world.fund(c, 100)
    service.place_bid(world.conn, c, world.upids[0], "CB", 15, "web", WED_NOON, CAL)
    # prospect 1 gets signed elsewhere (a roster mirror row would do the same) before resolution
    world.conn.execute("""insert into contracts (upid, team_abbr, kind, prospect_type, acquired_via, valid_from)
                          values (%s, %s, 'prospect', 'PC', 'pad', now())""", (world.upids[0], c))
    out = service.resolve_week(world.conn, WEEK, "scheduler", SUN_NOON)
    summary = out["summary"]
    assert [r["upid"] for r in summary["results"]] == [world.upids[1]]
    assert summary["unsold"] == [{"upid": world.upids[0], "name": f"Test {world.upids[0]}",
                                  "reason": "already owned by a team"}]
    assert world.count("select count(*) from wb_ledger where kind = 'auction_win' and upid = %s", (world.upids[0],)) == 0
    assert world.count("select count(*) from auction_results where week_start = %s", (WEEK,)) == 1


def test_a2_open_roster_assignment_also_counts(world):
    a, b = world.teams[:2]
    open_week(world, a, b)
    world.conn.execute("insert into roster_assignments (upid, team_abbr, valid_from, source) values (%s, %s, now(), %s)",
                       (world.upids[0], b, BY))
    try:
        out = service.resolve_week(world.conn, WEEK, "scheduler", SUN_NOON)
    finally:
        world.conn.execute("delete from roster_assignments where upid like 'auction-test-%'")
    assert [r["upid"] for r in out["summary"]["results"]] == [world.upids[1]]
    assert out["summary"]["unsold"][0]["upid"] == world.upids[0]


# ---------------------------------------------------------------- A3

def test_a3_stale_balance_is_a_rule_error_and_rolls_back(world, monkeypatch):
    a, b = world.teams[:2]
    open_week(world, a, b, amount=60)
    world.debit(a, 80)                                    # a has 20 left; a stale read still says 100
    monkeypatch.setattr(service, "_balance", lambda conn, team: 100)
    with pytest.raises(actions.RuleError, match="insufficient WizBucks"):
        service.resolve_week(world.conn, WEEK, "scheduler", SUN_NOON)
    monkeypatch.undo()
    assert world.conn.execute("select status from auction_weeks where week_start = %s", (WEEK,)).fetchone()[0] == "open"
    assert world.count("select count(*) from auction_results where week_start = %s", (WEEK,)) == 0
    assert world.count("select count(*) from contracts where upid like 'auction-test-%'") == 0
    assert world.count("select count(*) from wb_ledger where kind = 'auction_win'") == 0
    out = service.resolve_week(world.conn, WEEK, "scheduler", SUN_NOON)   # a is dropped, b still wins
    assert [r["team"] for r in out["summary"]["results"]] == [b]


def test_a3_team_locks_are_taken_sorted_before_balances_are_read(world, monkeypatch):
    a, b = world.teams[:2]
    open_week(world, a, b)
    log = []
    real_lock, real_balance = actions._lock, service._balance
    monkeypatch.setattr(actions, "_lock", lambda conn, *t: (log.append(("lock", t)), real_lock(conn, *t))[1])
    monkeypatch.setattr(service, "_balance", lambda conn, t: (log.append(("balance", t)), real_balance(conn, t))[1])
    service.resolve_week(world.conn, WEEK, "scheduler", SUN_NOON)
    first_balance = next(i for i, x in enumerate(log) if x[0] == "balance")
    team_locks = [x[1] for x in log[:first_balance] if x[0] == "lock"]
    assert team_locks and set(team_locks[0]) == {a, b}      # every team with a bid, locked before any balance read


# ---------------------------------------------------------------- A4

def test_a4_admin_only_functions(world):
    a, b = world.teams[:2]
    mgr = plain_team(world)
    open_week(world, a, b)
    bid_id = world.conn.execute("select bid_id from auction_bids where upid = %s", (world.upids[0],)).fetchone()[0]
    with pytest.raises(service.NotAdmin):
        service.admin_remove_bid(world.conn, bid_id, mgr)
    with pytest.raises(service.NotAdmin):
        service.admin_set_priority(world.conn, WEEK, world.order(), mgr)
    with pytest.raises(service.NotAdmin):
        service.place_bid(world.conn, a, world.upids[2], "OB", 10, "admin", MON_4PM, CAL, world.source(), actor=mgr)
    with pytest.raises(service.NotAdmin):
        service.place_bid(world.conn, a, world.upids[2], "OB", 10, "admin", MON_4PM, CAL, world.source())
    with pytest.raises(service.NotAdmin):
        service.resolve_week(world.conn, WEEK, mgr, SUN_NOON)
    with pytest.raises(service.NotAdmin):
        service.resolve_week(world.conn, WEEK, "scheduler", SUN_NOON, force=True)
    assert world.count("select count(*) from auction_bids where upid = %s and removed_at is not null", (world.upids[0],)) == 0
    assert issubclass(service.NotAdmin, BidError)

    # the admin versions work
    world.fund(world.teams[2], 100)
    service.place_bid(world.conn, world.teams[2], world.upids[2], "OB", 10, "admin", MON_4PM, CAL, world.source(), actor=ADMIN)
    assert world.count("select count(*) from auction_bids where source = 'admin' and upid = %s", (world.upids[2],)) == 1
    out = service.resolve_week(world.conn, WEEK, ADMIN, SUN_NOON)
    assert out["summary"]["resolved_by"] == ADMIN


# ---------------------------------------------------------------- A5

def test_a5_resolve_now_before_processing_is_refused(world):
    a, b = world.teams[:2]
    open_week(world, a, b)
    for when in (WED_NOON, SAT_NOON, at(6, 9, 59)):
        for cal in (CAL, None):
            with pytest.raises(BidError, match="Sun 10:00am"):
                service.resolve_week(world.conn, WEEK, ADMIN, when, calendar=cal)
    with pytest.raises(BidError):
        service.resolve_week(world.conn, WEEK, "scheduler", WED_NOON, calendar=CAL)
    assert world.conn.execute("select status from auction_weeks where week_start = %s", (WEEK,)).fetchone()[0] == "open"
    # allowed: processing, the Monday after, and an admin's force
    assert rules.phase_for(SUN_NOON, CAL).name == rules.PROCESSING
    service.resolve_week(world.conn, WEEK, "scheduler", SUN_NOON, calendar=CAL)


def test_a5_week_that_has_ended_and_force(world):
    a, b = world.teams[:2]
    open_week(world, a, b)
    out = service.resolve_week(world.conn, WEEK, "scheduler", at(7, 16), calendar=CAL)   # next Monday 4pm
    assert out["already_resolved"] is False


def test_a5_admin_force_overrides(world):
    a, b = world.teams[:2]
    open_week(world, a, b)
    out = service.resolve_week(world.conn, WEEK, ADMIN, WED_NOON, calendar=CAL, force=True)
    assert out["already_resolved"] is False and len(out["summary"]["results"]) == 2


# ---------------------------------------------------------------- A6

def test_a6_get_board_is_one_read_only_snapshot(world, monkeypatch):
    a = world.teams[0]
    world.fund(a, 100)
    seen = {}
    real = service._bid_rows

    def probe(conn, ws):
        seen["iso"] = conn.execute("show transaction_isolation").fetchone()[0]
        seen["ro"] = conn.execute("show transaction_read_only").fetchone()[0]
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("insert into auction_weeks (week_start, season, priority_order) values ('2030-01-07', 2030, '{}')")
        raise Rollback()  # the failed statement aborted the transaction anyway
    reader = connect(world.url)
    try:
        service.place_bid(world.conn, a, world.upids[0], "OB", 10, "web", MON_4PM, CAL, world.source())
        monkeypatch.setattr(service, "_bid_rows", probe)
        with pytest.raises(Rollback):
            service.get_board(reader, MON_4PM, CAL)
        assert seen == {"iso": "repeatable read", "ro": "on"}
        monkeypatch.setattr(service, "_bid_rows", real)
        assert service.get_board(reader, MON_4PM, CAL)["prospects"][0]["ob_team"] == a   # connection is usable again
    finally:
        reader.close()


# ---------------------------------------------------------------- A7

def test_a7_ties_in_placed_at_keep_insertion_order(world):
    a, b = world.teams[:2]
    open_week(world, a, b)        # both OBs share one placed_at
    rows = service._bid_rows(world.conn, WEEK)
    assert [r["team_abbr"] for r in rows] == [a, b]
    seqs = [world.conn.execute("select seq from auction_bids where bid_id = %s", (r["bid_id"],)).fetchone()[0] for r in rows]
    assert seqs[0] < seqs[1]


@pytest.mark.parametrize("amount", [10.5, 10.0, "ten", "10.5", None, True, -5, 0])
def test_a7_bad_amounts_are_bid_errors(world, amount):
    a = world.teams[0]
    world.fund(a, 100)
    with pytest.raises(BidError):
        service.place_bid(world.conn, a, world.upids[0], "OB", amount, "web", MON_4PM, CAL, world.source())
    assert world.count("select count(*) from auction_bids where upid like 'auction-test-%'") == 0


def test_a7_digit_string_amount_is_ok(world):
    a = world.teams[0]
    world.fund(a, 100)
    assert service.place_bid(world.conn, a, world.upids[0], "OB", "12", "web", MON_4PM, CAL, world.source())["amount"] == 12


@pytest.mark.parametrize("source", ["sms", "", None, "WEB"])
def test_a7_bad_source_is_a_bid_error(world, source):
    a = world.teams[0]
    world.fund(a, 100)
    with pytest.raises(BidError, match="source"):
        service.place_bid(world.conn, a, world.upids[0], "OB", 10, source, MON_4PM, CAL, world.source())


def test_a7_removed_at_and_removed_by_come_as_a_pair(world):
    a, b = world.teams[:2]
    open_week(world, a, b)
    for sql in ("update auction_bids set removed_at = now() where upid = %s",
                "update auction_bids set removed_by = 'x' where upid = %s"):
        with pytest.raises(psycopg.errors.CheckViolation):
            world.conn.execute(sql, (world.upids[0],))


def test_a7_nothing_can_be_inserted_into_a_resolved_week(world):
    a, b = world.teams[:2]
    open_week(world, a, b)
    service.resolve_week(world.conn, WEEK, "scheduler", SUN_NOON)
    with pytest.raises(psycopg.errors.RaiseException, match="resolved"):
        world.conn.execute("""insert into auction_bids (week_start, team_abbr, upid, bid_type, amount, placed_date_et, source)
                              values (%s, %s, %s, 'OB', 10, %s, 'web')""", (WEEK, world.teams[2], world.upids[2], WEEK))
    with pytest.raises(psycopg.errors.RaiseException, match="resolved"):
        world.conn.execute("""insert into auction_decisions (week_start, upid, team_abbr, decision, source)
                              values (%s, %s, %s, 'match', 'web')""", (WEEK, world.upids[0], a))


def test_a7_an_auction_win_can_only_be_debited_once(world):
    a, b = world.teams[:2]
    world.fund(a, 100)
    world.fund(b, 100)
    args = (25, "auction_win", "IN_SEASON", 2027, world.upids[0], "auction", WEEK.isoformat(), "dup", BY)
    world.conn.execute("select wb_spend(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (a, *args))
    with pytest.raises(psycopg.errors.UniqueViolation, match="wb_ledger_auction_win_once"):
        world.conn.execute("select wb_spend(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (b, *args))


# ---------------------------------------------------------------- A8

def test_a8_record_decision_rules(world):
    a, b, c = world.teams[:3]
    open_week(world, a, b)
    world.fund(c, 100)
    p1 = world.upids[0]
    service.place_bid(world.conn, c, p1, "CB", 20, "web", WED_NOON, CAL)

    with pytest.raises(BidError, match="only allowed Sat"):                       # phase gate
        service.record_decision(world.conn, a, p1, "match", "web", THU_NOON, CAL)
    with pytest.raises(BidError, match="Match or Forfeit"):                        # bad word
        service.record_decision(world.conn, a, p1, "maybe", "web", SAT_NOON, CAL)
    with pytest.raises(BidError, match="originating manager"):                     # only the OB team
        service.record_decision(world.conn, c, p1, "match", "web", SAT_NOON, CAL)
    with pytest.raises(BidError, match="no originating bid"):
        service.record_decision(world.conn, a, world.upids[2], "match", "web", SAT_NOON, CAL)
    out = service.record_decision(world.conn, a, p1, " Match ", "web", SAT_NOON, CAL)
    assert out["decision"] == "match" and "final" in out["message"]
    with pytest.raises(BidError, match="final"):                                   # final, even to change it
        service.record_decision(world.conn, a, p1, "forfeit", "web", SAT_NOON, CAL)
    assert world.count("select count(*) from auction_decisions where upid = %s", (p1,)) == 1

    res = service.resolve_week(world.conn, WEEK, "scheduler", SUN_NOON)
    won = {r["upid"]: (r["team"], r["outcome"]) for r in res["summary"]["results"]}
    assert won[p1] == (a, "OB_MATCH")
    with pytest.raises(BidError, match="already resolved"):
        service.record_decision(world.conn, a, p1, "match", "web", SAT_NOON, CAL)


def test_a8_admin_remove_bid(world):
    a, b, c = world.teams[:3]
    open_week(world, a, b)
    world.fund(c, 100)
    p1 = world.upids[0]
    ob = service._bid_rows(world.conn, WEEK)[0]["bid_id"]
    cb = service.place_bid(world.conn, c, p1, "CB", 20, "web", WED_NOON, CAL)["bid_id"]
    with pytest.raises(BidError, match="Remove the challenge bids"):
        service.admin_remove_bid(world.conn, ob, ADMIN)
    with pytest.raises(BidError, match="No such bid"):
        service.admin_remove_bid(world.conn, "00000000-0000-0000-0000-000000000000", ADMIN)
    service.admin_remove_bid(world.conn, cb, ADMIN, note="typo")
    row = world.conn.execute("select removed_by, admin_note, removed_at is not null from auction_bids where bid_id = %s", (cb,)).fetchone()
    assert row == (ADMIN, "typo", True)
    with pytest.raises(BidError, match="already removed"):
        service.admin_remove_bid(world.conn, cb, ADMIN)
    assert [r["bid_id"] for r in service._bid_rows(world.conn, WEEK) if str(r["upid"]) == p1] == [ob]   # row kept, not live
    service.admin_remove_bid(world.conn, ob, ADMIN)
    service.resolve_week(world.conn, WEEK, "scheduler", SUN_NOON)
    with pytest.raises(BidError, match="already resolved"):
        service.admin_remove_bid(world.conn, service._bid_rows(world.conn, WEEK)[0]["bid_id"], ADMIN)


def test_a8_admin_set_priority(world):
    a, b = world.teams[:2]
    open_week(world, a, b)
    new = list(reversed(world.order()))
    out = service.admin_set_priority(world.conn, WEEK, new, ADMIN)
    assert out["priority_order"] == new
    assert list(world.conn.execute("select priority_order from auction_weeks where week_start = %s", (WEEK,)).fetchone()[0]) == new
    with pytest.raises(BidError, match="every active team"):
        service.admin_set_priority(world.conn, WEEK, new[:-1], ADMIN)
    with pytest.raises(BidError, match="twice"):
        service.admin_set_priority(world.conn, WEEK, new + [new[0]], ADMIN)
    with pytest.raises(BidError, match="No auction week"):
        service.admin_set_priority(world.conn, "2030-01-07", new, ADMIN)
    service.resolve_week(world.conn, WEEK, "scheduler", SUN_NOON)
    with pytest.raises(BidError, match="already resolved"):
        service.admin_set_priority(world.conn, WEEK, new, ADMIN)


def test_a8_mark_announced(world):
    a, b = world.teams[:2]
    open_week(world, a, b)
    assert service.mark_announced(world.conn, WEEK) is False          # not resolved yet
    service.resolve_week(world.conn, WEEK, "scheduler", SUN_NOON)
    assert service.mark_announced(world.conn, WEEK) is True
    assert service.mark_announced(world.conn, WEEK) is False
    assert service.mark_announced(world.conn, "2030-01-07") is False


def test_a8_bid_on_a_resolved_week(world):
    a, b, c = world.teams[:3]
    open_week(world, a, b)
    world.fund(c, 100)
    service.resolve_week(world.conn, WEEK, ADMIN, WED_NOON, calendar=CAL, force=True)   # resolved in an open phase
    with pytest.raises(BidError, match="already resolved"):
        service.place_bid(world.conn, c, world.upids[2], "OB", 10, "web", WED_NOON, CAL, world.source())
    with pytest.raises(BidError, match="already resolved"):
        service.place_bid(world.conn, c, world.upids[0], "CB", 30, "web", WED_NOON, CAL)
    assert world.count("select count(*) from auction_bids where upid = %s", (world.upids[2],)) == 0
