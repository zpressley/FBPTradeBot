"""Spec 03 tests 1-12 (specs/03_rosters_contracts_wizbucks.md, "Tests").

Run against a local Postgres with schema v1 + 0003 applied. Test names carry the spec's numbers.
"""

import threading
import time
from datetime import datetime, timezone

import psycopg
import pytest

from league import actions as A
from league.actions import RuleError
from tests.league.conftest import check_balances

S = 2027
BY = "test"


# ------------------------------------------------------------------ helpers

def mk_player(conn, upid, positions="OF", name=None, carried_tier=None):
    conn.execute(
        "insert into players (upid, name, positions, mlb_primary_position, carried_tier) values (%s, %s, %s, %s, %s)",
        (upid, name or f"Test {upid}", positions, positions.split(",")[0], carried_tier),
    )
    return upid


def mk_keeper(conn, upid, team, tier, slot="active", **extra):
    mk_player(conn, upid)
    conn.execute("insert into contracts (upid, team_abbr, kind, tier, acquired_via) values (%s, %s, 'keeper', %s, 'import')",
                 (upid, team, tier))
    conn.execute("insert into roster_slots (upid, slot) values (%s, %s)", (upid, slot))
    return upid


def mk_prospect(conn, upid, team, ptype, international=False):
    mk_player(conn, upid)
    conn.execute("""insert into contracts (upid, team_abbr, kind, prospect_type, acquired_via, international_rookie)
                    values (%s, %s, 'prospect', %s, 'pad', %s)""", (upid, team, ptype, international))
    conn.execute("insert into roster_slots (upid, slot) values (%s, 'farm')", (upid,))
    return upid


def fund(conn, team, amount):
    A.wb_credit(conn, team, amount, "opening_balance", "PAD", S, created_by=BY)


def bal(conn, team):
    return A.balance(conn, team)


def contract(conn, upid):
    with conn.cursor() as cur:
        cur.execute("""select team_abbr, kind, tier, prospect_type, il_tag, il_tag_season, rat_count, end_reason, valid_to
                         from contracts where upid = %s order by id desc limit 1""", (upid,))
        r = cur.fetchone()
    keys = "team kind tier ptype il_tag il_tag_season rat_count end_reason valid_to".split()
    return dict(zip(keys, r)) if r else None


def ledger_count(conn, team=None):
    with conn.cursor() as cur:
        cur.execute("select count(*) from wb_ledger where %s::text is null or team_abbr = %s", (team, team))
        return cur.fetchone()[0]


def utc(y, m, d, h=16):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def set_debut(conn, upid, day):
    conn.execute("""insert into prospect_status (upid, mlb_debut, updated_at) values (%s, %s, now())
                    on conflict (upid) do update set mlb_debut = excluded.mlb_debut""", (upid, day))


# -------------------------------------------------------------------- tests

def test_01_balance_equals_ledger_sum(conn):
    fund(conn, "WIZ", 100)
    fund(conn, "HAM", 40)
    A.wb_spend(conn, "WIZ", 30, "buyin", "PAD", S, created_by=BY)
    A.wb_credit(conn, "HAM", 15, "installment", "KAP", S, created_by=BY)
    assert bal(conn, "WIZ") == 70 and bal(conn, "HAM") == 55
    check_balances(conn)  # also runs after every test by the fixture


def test_02_overspend_fails_with_no_ledger_row(conn):
    fund(conn, "WIZ", 20)
    before = ledger_count(conn, "WIZ")
    with pytest.raises(RuleError, match=r"insufficient WizBucks: balance 20, needed 30"):
        A.wb_spend(conn, "WIZ", 30, "auction_win", "APA", S, created_by=BY)
    assert ledger_count(conn, "WIZ") == before
    assert bal(conn, "WIZ") == 20
    # a multi-step action that runs dry halfway undoes its first step too
    mk_keeper(conn, "test-rat-1", "WIZ", "FC-1")
    with pytest.raises(RuleError, match="insufficient"):
        A.reduce_a_tier(conn, "test-rat-1", created_by=BY, season=S)
    assert ledger_count(conn, "WIZ") == before and contract(conn, "test-rat-1")["tier"] == "FC-1"


def test_02b_draft_slots_are_prepaid(conn):
    fund(conn, "WIZ", 100)
    A.buy_draft_slot(conn, "WIZ", "BC", created_by=BY, season=S, count=2)
    A.buy_draft_slot(conn, "WIZ", "DC", created_by=BY, season=S, count=3)
    assert bal(conn, "WIZ") == 100 - 55  # 2 x $20 + 3 x $5
    picks = [(1, "BC"), (2, "BC"), (3, "DC"), (4, "DC"), (5, "DC")]
    for i, (rnd, ptype) in enumerate(picks):
        upid = mk_player(conn, f"test-draft-{i}")
        r = A.assign_prospect_contract(conn, upid, "WIZ", acquired_via="prospect_draft", draft_round=rnd,
                                       created_by=BY, season=S)
        assert r["prospect_type"] == ptype and r["charged"] == 0
        assert bal(conn, "WIZ") == 45  # drafting into a slot charges nothing more
    extra = mk_player(conn, "test-draft-x")
    with pytest.raises(RuleError, match="no open DC draft slot"):
        A.assign_prospect_contract(conn, extra, "WIZ", acquired_via="prospect_draft", draft_round=3,
                                   created_by=BY, season=S)
    assert contract(conn, extra) is None and bal(conn, "WIZ") == 45
    with conn.cursor() as cur:
        cur.execute("select count(*) from prospect_draft_slots where team_abbr = 'WIZ' and filled_upid is not null")
        assert cur.fetchone()[0] == 5


def test_02c_dc_slot_cap(conn):
    fund(conn, "WIZ", 200)
    A.buy_draft_slot(conn, "WIZ", "DC", created_by=BY, season=S, count=15)
    with pytest.raises(RuleError, match="at most 15 DC slots"):
        A.buy_draft_slot(conn, "WIZ", "DC", created_by=BY, season=S)


def test_03_rollover_caps_and_forfeits(conn):
    fund(conn, "WIZ", 50)
    r = A.close_phase_rollover(conn, S, "PAD", "KAP", created_by="system")
    row = next(t for t in r["teams"] if t["team"] == "WIZ")
    assert (row["carried"], row["forfeited"]) == (30, 20)
    assert bal(conn, "WIZ") == 30
    with conn.cursor() as cur:
        cur.execute("select kind, amount, phase, note from wb_ledger where team_abbr = 'WIZ' and ref_type = 'rollover' order by id")
        rows = cur.fetchall()
    assert [(k, a) for k, a, _, _ in rows] == [("rollover_forfeit", -20), ("rollover_in", 0)]
    assert "carried $30" in rows[1][3]
    assert bal(conn, "WIZ") == 30   # the cap: the excess left, the carried part never moved
    n = ledger_count(conn)
    A.close_phase_rollover(conn, S, "PAD", "KAP", created_by="system")  # safe to run twice
    assert ledger_count(conn) == n and bal(conn, "WIZ") == 30
    # KAP -> APA cap is $100
    fund(conn, "HAM", 130)
    A.close_phase_rollover(conn, S, "KAP", "APA", created_by="system")
    assert bal(conn, "HAM") == 100


def test_04_keep_advances_tier(conn):
    fund(conn, "WIZ", 200)
    mk_keeper(conn, "test-k-tc2", "WIZ", "TC-2")
    mk_keeper(conn, "test-k-fc2", "WIZ", "FC-2")
    r = A.keep_player(conn, "test-k-tc2", created_by=BY, season=S)
    assert r["charged"] == 25 and contract(conn, "test-k-tc2")["tier"] == "VC-1"
    r = A.keep_player(conn, "test-k-fc2", created_by=BY, season=S)
    assert r["charged"] == 125 and contract(conn, "test-k-fc2")["tier"] == "FC-2"
    assert bal(conn, "WIZ") == 50
    with pytest.raises(RuleError, match="already kept"):
        A.keep_player(conn, "test-k-tc2", created_by=BY, season=S)
    with pytest.raises(RuleError, match="is not on HAM"):
        A.keep_player(conn, "test-k-fc2", created_by=BY, season=S + 1, team="HAM")


def test_05_il_tag(conn):
    fund(conn, "WIZ", 200)
    mk_keeper(conn, "test-il-vc2", "WIZ", "VC-2")
    mk_keeper(conn, "test-il-tcr", "WIZ", "TC-R")
    mk_keeper(conn, "test-il-vc1", "WIZ", "VC-1")
    r = A.il_tag(conn, "test-il-vc2", created_by=BY, approved_by="zach", season=S)
    assert r["charged"] == 40 and contract(conn, "test-il-vc2")["tier"] == "FC-1"
    c = contract(conn, "test-il-vc2")
    assert c["il_tag"] and c["il_tag_season"] == S
    with pytest.raises(RuleError, match="can't take an IL tag"):
        A.il_tag(conn, "test-il-tcr", created_by=BY, approved_by="zach", season=S)
    assert contract(conn, "test-il-tcr")["tier"] == "TC-R" and bal(conn, "WIZ") == 160
    with pytest.raises(RuleError, match="already used its VC IL tag"):
        A.il_tag(conn, "test-il-vc1", created_by=BY, approved_by="zach", season=S)
    with pytest.raises(RuleError, match="admin must approve"):
        A.il_tag(conn, "test-il-vc1", created_by=BY, approved_by=None, season=S)


def test_06_reduce_a_tier(conn):
    fund(conn, "WIZ", 500)
    mk_keeper(conn, "test-rat-fc1", "WIZ", "FC-1")
    mk_keeper(conn, "test-rat-fc2", "WIZ", "FC-2")
    mk_keeper(conn, "test-rat-tc2", "WIZ", "TC-2")
    mk_keeper(conn, "test-rat-vc1", "WIZ", "VC-1")
    r = A.reduce_a_tier(conn, "test-rat-fc1", created_by=BY, season=S)
    assert (r["salary"], r["rat_price"], r["charged"]) == (55, 75, 130)
    assert contract(conn, "test-rat-fc1")["tier"] == "FC-1" and contract(conn, "test-rat-fc1")["rat_count"] == 1
    r = A.reduce_a_tier(conn, "test-rat-fc2", created_by=BY, season=S)
    assert (r["salary"], r["rat_price"]) == (85, 75)
    assert contract(conn, "test-rat-fc2")["tier"] == "FC-2"
    before = bal(conn, "WIZ")
    for u in ("test-rat-tc2", "test-rat-vc1"):
        with pytest.raises(RuleError, match="can't be reduced"):
            A.reduce_a_tier(conn, u, created_by=BY, season=S)
    assert bal(conn, "WIZ") == before
    with conn.cursor() as cur:  # the RaT is its own ledger row, so the KAP tax base (keeper_salary rows) excludes it
        cur.execute("select kind, amount from wb_ledger where upid = 'test-rat-fc1' order by id")
        assert cur.fetchall() == [("rat", -75), ("keeper_salary", -55)]


def test_07_dc_debut_fee_void_and_september(conn):
    fund(conn, "WIZ", 100)
    a = mk_prospect(conn, "test-dc-paid", "WIZ", "DC")
    b = mk_prospect(conn, "test-dc-void", "WIZ", "DC")
    c = mk_prospect(conn, "test-dc-sept", "WIZ", "DC")
    set_debut(conn, a, "2027-06-10")
    set_debut(conn, b, "2027-06-10")
    set_debut(conn, c, "2027-09-05")
    # paid on day 4: becomes PC, $15
    r = A.pay_debut_fee(conn, a, created_by=BY, season=S, now=utc(2027, 6, 14))
    assert r["charged"] == 15 and contract(conn, a)["ptype"] == "PC" and bal(conn, "WIZ") == 85
    # inside the window the system can't void
    with pytest.raises(RuleError, match="window is open"):
        A.void_dc(conn, b, season=S, now=utc(2027, 6, 14))
    # day 8: too late to pay, voided, goes to the auction pool
    with pytest.raises(RuleError, match="window closed"):
        A.pay_debut_fee(conn, b, created_by=BY, season=S, now=utc(2027, 6, 18))
    r = A.void_dc(conn, b, season=S, now=utc(2027, 6, 18))
    assert r["voided"] and r["to_auction_pool"]
    assert contract(conn, b)["end_reason"] == "voided"
    with conn.cursor() as cur:
        cur.execute("select count(*) from contracts where upid = %s and valid_to is null", (b,))
        assert cur.fetchone()[0] == 0
        cur.execute("select update_type from player_events where upid = %s and source_file = 'league_actions'", (b,))
        assert ("dc_voided",) in cur.fetchall()
    # September debut: never voided, no fee charged
    with pytest.raises(RuleError, match="September"):
        A.void_dc(conn, c, season=S, now=utc(2027, 10, 20))
    with pytest.raises(RuleError, match="September"):
        A.pay_debut_fee(conn, c, created_by=BY, season=S, now=utc(2027, 9, 8))
    assert A.void_overdue_dcs(conn, season=S, now=utc(2027, 10, 20))["voided"] == 0
    assert contract(conn, c)["ptype"] == "DC" and contract(conn, c)["valid_to"] is None
    assert bal(conn, "WIZ") == 85


def test_08_graduation_tiers_and_advancement(conn):
    fund(conn, "WIZ", 100)
    bc = mk_prospect(conn, "test-g-bc", "WIZ", "BC")
    pc = mk_prospect(conn, "test-g-pc", "WIZ", "PC")
    intl = mk_prospect(conn, "test-g-intl", "WIZ", "PC", international=True)
    assert A.graduate(conn, bc, created_by=BY, season=S)["tier"] == "TC-BC-1"
    assert A.graduate(conn, pc, created_by=BY, season=S)["tier"] == "TC-R"
    assert A.graduate(conn, intl, created_by=BY, season=S, slot="na")["tier"] == "TC-1"
    assert contract(conn, bc)["kind"] == "keeper"
    with conn.cursor() as cur:
        cur.execute("select slot from roster_slots where upid = %s", (intl,))
        assert cur.fetchone()[0] == "na"
        cur.execute("select end_reason from contracts where upid = %s and kind = 'prospect'", (bc,))
        assert cur.fetchone()[0] == "graduated"
    # TC-BC-1 -> TC-BC-2 -> TC-1 over two KAPs, $5 each time
    assert A.keep_player(conn, bc, created_by=BY, season=S)["charged"] == 5
    assert contract(conn, bc)["tier"] == "TC-BC-2"
    assert A.keep_player(conn, bc, created_by=BY, season=S + 1)["charged"] == 5
    assert contract(conn, bc)["tier"] == "TC-1"
    # forced graduation needs an actual limit breach
    p2 = mk_prospect(conn, "test-g-forced", "WIZ", "PC")
    conn.execute("insert into prospect_status (upid, pa, updated_at) values (%s, 200, now())", (p2,))
    with pytest.raises(RuleError, match="not over any FBP prospect limit"):
        A.graduate(conn, p2, created_by="system", season=S, forced=True)
    conn.execute("update prospect_status set pa = 351 where upid = %s", (p2,))
    assert A.graduate(conn, p2, created_by="system", season=S, forced=True)["tier"] == "TC-R"


def test_09_release_inherit_and_draft_round(conn):
    fund(conn, "WIZ", 50)
    mk_keeper(conn, "test-r-vc2", "WIZ", "VC-2")
    r = A.release(conn, "test-r-vc2", created_by=BY, season=S)
    assert r["carried_tier"] == "VC-2" and contract(conn, "test-r-vc2")["end_reason"] == "released"
    r = A.acquire_free_agent(conn, "test-r-vc2", "HAM", created_by=BY, season=S)
    assert r["tier"] == "VC-2" and contract(conn, "test-r-vc2")["team"] == "HAM"
    with conn.cursor() as cur:
        cur.execute("select carried_tier from players where upid = 'test-r-vc2'")
        assert cur.fetchone()[0] is None  # one fact, one place: the tier lives on the open contract now
    fa = mk_player(conn, "test-r-none")
    assert A.acquire_free_agent(conn, fa, "HAM", created_by=BY, season=S)["tier"] == "TC-1"
    with pytest.raises(RuleError, match="owned"):
        A.acquire_free_agent(conn, fa, "WIZ", created_by=BY, season=S)
    # a round 2 Keeper Draft pick is VC-1 whatever he carried; round 5 is TC-1
    d2 = mk_player(conn, "test-r-draft2", carried_tier="FC-1")
    assert A.assign_keeper_draft_pick(conn, d2, "WIZ", 2, created_by=BY, season=S)["tier"] == "VC-1"
    d5 = mk_player(conn, "test-r-draft5", carried_tier="FC-2")
    assert A.assign_keeper_draft_pick(conn, d5, "WIZ", 5, created_by=BY, season=S)["tier"] == "TC-1"
    # a not-kept player at KAP close keeps his tier too
    mk_keeper(conn, "test-r-nk", "WIZ", "TC-2")
    assert A.release(conn, "test-r-nk", created_by="system", reason="not_kept", season=S)["carried_tier"] == "TC-2"
    assert contract(conn, "test-r-nk")["end_reason"] == "not_kept"


def test_10_trade_carries_il_tag_and_rat_history(conn):
    fund(conn, "WIZ", 500)
    mk_keeper(conn, "test-t-1", "WIZ", "FC-1")
    A.reduce_a_tier(conn, "test-t-1", created_by=BY, season=S)           # FC-1 -> kept at VC-2 -> FC-1, rat_count 1
    A.il_tag(conn, "test-t-1", created_by=BY, approved_by="zach", season=S + 1)  # FC-1, $50 -> FC-2
    before = contract(conn, "test-t-1")
    assert before["il_tag"] and before["rat_count"] == 1 and before["tier"] == "FC-2"
    A.transfer(conn, "test-t-1", "WIZ", "HAM", created_by="trade-service", season=S + 1, ref_id="T-1")
    after = contract(conn, "test-t-1")
    assert after["team"] == "HAM" and after["valid_to"] is None
    assert (after["tier"], after["il_tag"], after["il_tag_season"], after["rat_count"]) == ("FC-2", True, S + 1, 1)
    with conn.cursor() as cur:
        cur.execute("select team_abbr, end_reason from contracts where upid = 'test-t-1' order by id")
        assert cur.fetchall()[-2:] == [("WIZ", "traded"), ("HAM", None)]
    with pytest.raises(RuleError, match="is not on WIZ"):
        A.transfer(conn, "test-t-1", "WIZ", "DMN", created_by="trade-service", season=S)


def test_11_roster_check_flags_but_charges_nothing(conn):
    # 27 active: 9 SP (no SP/RP swing) + 5 RP + 13 batters including one catcher
    n = 0
    for pos, count in (("SP", 9), ("RP", 5), ("C", 1), ("OF", 12)):
        for _ in range(count):
            mk_keeper(conn, f"test-ros-{n}", "WIZ", "TC-1")
            conn.execute("update players set positions = %s where upid = %s", (pos, f"test-ros-{n}"))
            n += 1
    assert n == 27
    rows = ledger_count(conn)
    res = A.roster_check(conn, season=S, team="WIZ")
    rules = {v["rule"]: v for v in res["violations"]}
    assert set(rules) == {"active_max", "sp_max"}, rules
    assert "27 active" in rules["active_max"]["detail"] and "9 SP" in rules["sp_max"]["detail"]
    assert rules["sp_max"]["penalty_amount"] == 20
    assert ledger_count(conn) == rows and bal(conn, "WIZ") == 0   # nothing charged until an admin confirms
    # a swing pitcher raises the SP cap to 9
    conn.execute("update players set positions = 'SP,RP' where upid = 'test-ros-0'")
    assert "sp_max" not in {v["rule"] for v in A.roster_check(conn, season=S, team="WIZ")["violations"]}
    # the system can't fine; an admin confirms, and a penalty may overdraw
    with pytest.raises(RuleError, match="confirmed by an admin"):
        A.penalty(conn, "WIZ", 20, rule="active_max", created_by="system", season=S)
    A.penalty(conn, "WIZ", 20, rule="active_max", created_by="zach", season=S, dates="2027-04-02")
    assert bal(conn, "WIZ") == -20
    # moving a 27th player into active is refused up front
    extra = mk_keeper(conn, "test-ros-extra", "WIZ", "TC-1", slot="il")
    with pytest.raises(RuleError, match="27 active|can't go active"):
        A.move_slot(conn, extra, "active", created_by=BY, season=S)
    A.move_slot(conn, "test-ros-1", "il", created_by=BY, season=S)
    assert A.move_slot(conn, "test-ros-1", "il", created_by=BY, season=S)["message"] == "already there"


def test_12_concurrent_spend_of_last_20(db_url):
    """Two real sessions each try to spend the team's last $20. Exactly one succeeds, nothing is left behind."""
    team = "ZZT"
    admin = psycopg.connect(db_url, autocommit=True)
    try:
        admin.execute("insert into teams (abbr, name, active) values (%s, 'Concurrency Test Team', false)", (team,))
        A.wb_credit(admin, team, 20, "opening_balance", "APA", S, created_by=BY)  # autocommit: committed
        results = {}
        barrier = threading.Barrier(2)

        def worker(label, kind):
            c = psycopg.connect(db_url, autocommit=False)
            try:
                c.execute("select 1")
                barrier.wait(timeout=10)
                try:
                    A.wb_spend(c, team, 20, kind, "APA", S, created_by=label)
                    time.sleep(0.4)          # hold the locks while the other session waits
                    c.commit()
                    results[label] = "ok"
                except RuleError as e:
                    c.rollback()
                    results[label] = str(e)
            finally:
                c.close()

        threads = [threading.Thread(target=worker, args=("trade", "trade_out")),
                   threading.Thread(target=worker, args=("auction", "auction_win"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert sorted(v == "ok" for v in results.values()) == [False, True], results
        loser = next(v for v in results.values() if v != "ok")
        assert "insufficient WizBucks: balance 0, needed 20" in loser
        assert A.balance(admin, team) == 0
        with admin.cursor() as cur:
            cur.execute("select count(*) from wb_ledger where team_abbr = %s and amount < 0", (team,))
            assert cur.fetchone()[0] == 1
    finally:
        admin.execute("set fbp.ledger_maintenance = 'on'")   # the ledger is append-only; test cleanup only
        admin.execute("delete from wb_ledger where team_abbr = %s", (team,))
        admin.execute("delete from teams where abbr = %s", (team,))
        with admin.cursor() as cur:
            cur.execute("select (select count(*) from wb_ledger where team_abbr = %s) + (select count(*) from teams where abbr = %s)",
                        (team, team))
            assert cur.fetchone()[0] == 0
        admin.close()


# ------------------------------------------------------- extra coverage

def test_installments_use_brackets_and_are_idempotent(conn):
    r = A.credit_installment(conn, S, "PAD", created_by="system")
    amounts = {t["team"]: t["amount"] + t["reward"] for t in r["teams"]}
    assert amounts["HAM"] == amounts["WIZ"] == amounts["LFB"] == amounts["DMN"] == 100
    assert amounts["SAD"] == 120 and amounts["JEP"] == 120 and amounts["B2J"] == 120 and amounts["TBB"] == 120
    assert amounts["CFL"] == 140 + 20 and amounts["WAR"] == 140 + 10 and amounts["RV"] == 140 and amounts["DRO"] == 140
    n = ledger_count(conn)
    A.credit_installment(conn, S, "PAD", created_by="system")
    assert ledger_count(conn) == n
    A.credit_installment(conn, S, "KAP", created_by="system")
    assert bal(conn, "SAD") == 120 + 375 + 50 and bal(conn, "JEP") == 120 + 375 + 25 and bal(conn, "WIZ") == 475


def test_upgrade_prospect_and_prices(conn):
    fund(conn, "WIZ", 100)
    p = mk_prospect(conn, "test-up-dc", "WIZ", "DC")
    assert A.upgrade_prospect(conn, p, "PC", created_by=BY, season=S)["charged"] == 5
    with pytest.raises(RuleError, match="finalization"):
        A.upgrade_prospect(conn, p, "BC", created_by=BY, season=S)
    A.open_window(conn, S, "BC_FINALIZATION", utc(2020, 1, 1), utc(2090, 1, 1), "zach")
    assert A.upgrade_prospect(conn, p, "BC", created_by=BY, season=S)["charged"] == 10
    with pytest.raises(RuleError, match="can't upgrade"):
        A.upgrade_prospect(conn, p, "PC", created_by=BY, season=S)
    n = mk_player(conn, "test-up-pad")
    assert A.assign_prospect_contract(conn, n, "WIZ", "BC", acquired_via="pad", created_by=BY, season=S)["charged"] == 20
    assert bal(conn, "WIZ") == 100 - 5 - 10 - 20


def test_retain_bc_and_admin_adjust(conn):
    fund(conn, "WIZ", 100)
    on, off = mk_prospect(conn, "test-bc-on", "WIZ", "BC"), mk_prospect(conn, "test-bc-off", "WIZ", "BC")
    conn.execute("""insert into top100_snapshots (snapshot_date, rank, upid, name, official)
                    values ('2026-11-01', 9999, %s, 'x', true)""", (on,))
    assert A.retain_bc(conn, on, season=S)["retained"] is True
    r = A.retain_bc(conn, off, season=S)
    assert r["retained"] is False and r["needs_decision"] and contract(conn, off)["ptype"] == "BC"
    assert A.retain_bc(conn, off, season=S, choice="repurchase")["charged"] == 20
    with pytest.raises(RuleError, match="needs a reason"):
        A.admin_adjust(conn, reason=" ", created_by="zach", team="WIZ", amount=5, season=S)
    A.admin_adjust(conn, reason="fix", created_by="zach", team="WIZ", amount=-30, season=S)
    assert bal(conn, "WIZ") == 100 - 20 - 30
    with pytest.raises(RuleError, match="insufficient"):
        A.admin_adjust(conn, reason="too much", created_by="zach", team="WIZ", amount=-500, season=S)
    mk_keeper(conn, "test-adj", "WIZ", "TC-1")
    A.admin_adjust(conn, reason="fix tier", created_by="zach", upid="test-adj", set_tier="VC-1", season=S)
    assert contract(conn, "test-adj")["tier"] == "VC-1"


def test_import_never_touches_live_tables(db_url):
    """The daily JSON import reloads the mirror but must not truncate wb_ledger or contracts."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    c = psycopg.connect(db_url, autocommit=True)
    try:
        A.admin_adjust(c, reason="survival test", created_by="test-import-survival", team="WIZ", amount=7, season=S)
        c.execute("insert into players (upid, name) values ('test-import-survivor', 'Survivor')")
        c.execute("""insert into contracts (upid, team_abbr, kind, tier, acquired_via)
                     values ('test-import-survivor', 'WIZ', 'keeper', 'TC-1', 'import')""")
        env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL_DIRECT"}
        env["DATABASE_URL"] = db_url
        out = subprocess.run([sys.executable, str(root / "scripts" / "db" / "import_json.py")], cwd=root, env=env,
                             capture_output=True, text=True, timeout=300)
        # the survivor player isn't in the JSON, so the import's stale-player delete must fail loudly
        # and roll back, leaving every live row in place
        assert out.returncode != 0
        assert A.balance(c, "WIZ") == 7
        assert c.execute("select count(*) from contracts where upid = 'test-import-survivor'").fetchone()[0] == 1
    finally:
        c.execute("delete from contracts where upid = 'test-import-survivor'")
        c.execute("set fbp.ledger_maintenance = 'on'")   # the ledger is append-only; test cleanup only
        c.execute("delete from wb_ledger where created_by = 'test-import-survival'")
        c.execute("delete from players where upid = 'test-import-survivor'")
        c.close()
    # with the live rows gone (or pointing only at JSON players) the import succeeds again
    assert subprocess.run([sys.executable, str(root / "scripts" / "db" / "import_json.py")], cwd=root, env=env,
                          capture_output=True, text=True, timeout=300).returncode == 0
