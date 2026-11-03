"""Reviewer fixes: ledger integrity in the database, wrapper whitelists, idempotent BC retention,
IL-tag allowance, window gating. Uses the helpers and the `conn` fixture from test_actions / conftest.
"""

from datetime import date, timedelta

import psycopg
import pytest

from league import actions as A
from league.actions import RuleError
from tests.league.test_actions import (BY, S, bal, contract, fund, ledger_count, mk_keeper, mk_player,
                                       mk_prospect, utc)

CHECK = psycopg.errors.CheckViolation
UNIQUE = psycopg.errors.UniqueViolation


def raw_ledger(conn, team, amount, kind, ref_type=None, ref_id=None):
    return conn.execute(
        """insert into wb_ledger (team_abbr, amount, kind, phase, season, ref_type, ref_id, created_by)
           values (%s, %s, %s, 'PAD', %s, %s, %s, 'raw-test') returning id""",
        (team, amount, kind, S, ref_type, ref_id)).fetchone()[0]


def must_fail(conn, exc, sql, params=()):
    """Run one statement in a savepoint and expect the database to refuse it."""
    with pytest.raises(exc):
        with conn.transaction():
            conn.execute(sql, params)


# ------------------------------------------------------- 1. ledger integrity in the database

def test_ledger_amount_sign_by_kind(conn):
    for kind, amount in (("installment", -5), ("trade_in", 0), ("auction_win", 5), ("keeper_salary", 5),
                         ("rat", 0), ("penalty", 5), ("rollover_forfeit", 5), ("rollover_in", -1),
                         ("opening_balance", -1)):
        with pytest.raises(CHECK):
            with conn.transaction():
                raw_ledger(conn, "WIZ", amount, kind)
    raw_ledger(conn, "WIZ", 50, "admin_adjustment")
    raw_ledger(conn, "WIZ", -10, "admin_adjustment")
    raw_ledger(conn, "WIZ", 0, "rollover_in")        # the legal zero-amount marker
    raw_ledger(conn, "WIZ", 0, "opening_balance")


def test_ledger_is_append_only(conn):
    lid = raw_ledger(conn, "WIZ", 10, "installment")
    for sql in ("update wb_ledger set amount = 99 where id = %s", "delete from wb_ledger where id = %s"):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            with conn.transaction():
                conn.execute(sql, (lid,))
    assert bal(conn, "WIZ") == 10


def test_direct_insert_overdraw_is_rejected_by_the_database(conn):
    raw_ledger(conn, "WIZ", 20, "installment")
    conn.execute("set constraints wb_ledger_balance_nonneg immediate")   # in production it fires at commit
    with pytest.raises(psycopg.errors.RaiseException, match="insufficient WizBucks"):
        with conn.transaction():
            raw_ledger(conn, "WIZ", -30, "auction_win")
    raw_ledger(conn, "WIZ", -30, "penalty")      # a penalty may overdraw
    assert bal(conn, "WIZ") == -10
    with pytest.raises(psycopg.errors.RaiseException, match="insufficient WizBucks"):
        with conn.transaction():
            raw_ledger(conn, "WIZ", -1, "buyin")  # and nothing else may spend a negative balance
    raw_ledger(conn, "WIZ", 5, "installment")     # credits are fine


def test_contract_constraints(conn):
    mk_player(conn, "c-1")
    base = ("insert into contracts (upid, team_abbr, kind, tier, acquired_via{cols}) "
            "values ('c-1', 'WIZ', 'keeper', 'TC-1', {via}{vals})")
    ok = base.format(cols="", via="'import'", vals="")
    must_fail(conn, CHECK, base.format(cols="", via="'bogus'", vals=""))
    must_fail(conn, CHECK, base.format(cols=", rat_count", via="'import'", vals=", -1"))
    must_fail(conn, CHECK, base.format(cols=", end_reason", via="'import'", vals=", 'released'"))          # reason but still open
    must_fail(conn, CHECK, base.format(cols=", valid_to", via="'import'", vals=", now()"))                 # closed with no reason
    must_fail(conn, CHECK, base.format(cols=", valid_to, end_reason", via="'import'",
                                       vals=", now() - interval '2 days', 'released'"))                   # valid_to < valid_from
    must_fail(conn, CHECK, base.format(cols=", valid_to, end_reason", via="'import'", vals=", now(), 'sold'"))
    must_fail(conn, CHECK, base.format(cols=", il_tag", via="'import'", vals=", true"))                    # tag without season/family/team
    must_fail(conn, CHECK, base.format(cols=", il_tag_season", via="'import'", vals=", 2027"))             # season without a tag
    must_fail(conn, CHECK, base.format(cols=", bc_decision", via="'import'", vals=", 'retained'"))
    must_fail(conn, CHECK, "insert into contracts (upid, team_abbr, kind, tier, acquired_via) "
                           "values ('c-1', 'WIZ', 'keeper', 'P', 'import')")
    conn.execute(ok)
    conn.execute("""update contracts set valid_to = now(), end_reason = 'released' where upid = 'c-1'""")


def test_other_table_constraints(conn):
    must_fail(conn, CHECK, "insert into wb_prices (season, item, amount) values (2099, 'x', -1)")
    must_fail(conn, CHECK, "update players set carried_tier = 'P' where upid = (select min(upid) from players)")
    fund(conn, "WIZ", 100)
    A.buy_draft_slot(conn, "WIZ", "DC", created_by=BY, season=S, count=2)
    s1, s2 = [r[0] for r in conn.execute("select id from prospect_draft_slots order by id").fetchall()][:2]
    mk_player(conn, "ds-1")
    mk_player(conn, "ds-2")
    must_fail(conn, CHECK, "update prospect_draft_slots set filled_upid = 'ds-1' where id = %s", (s1,))      # no filled_at
    must_fail(conn, CHECK, "update prospect_draft_slots set filled_at = now() where id = %s", (s1,))         # no filled_upid
    conn.execute("update prospect_draft_slots set filled_upid = 'ds-1', filled_at = now() where id = %s", (s1,))
    must_fail(conn, UNIQUE, "update prospect_draft_slots set filled_upid = 'ds-1', filled_at = now() where id = %s", (s2,))
    must_fail(conn, UNIQUE, "update prospect_draft_slots set ledger_id = (select ledger_id from prospect_draft_slots where id = %s) "
                            "where id = %s", (s1, s2))


# ------------------------------------------------------- 2. wb_spend / wb_credit wrappers

def test_wrappers_whitelist_kinds(conn):
    fund(conn, "WIZ", 100)
    n = ledger_count(conn)
    for kind in ("penalty", "admin_adjustment", "reversal", "installment", "bogus"):
        with pytest.raises(RuleError, match="wb_spend can't write"):
            A.wb_spend(conn, "WIZ", 10, kind, "PAD", S, created_by=BY)
    for kind in ("penalty", "admin_adjustment", "reversal", "buyin", "bogus"):
        with pytest.raises(RuleError, match="wb_credit can't write"):
            A.wb_credit(conn, "WIZ", 10, kind, "PAD", S, created_by=BY)
    with pytest.raises(RuleError, match="positive"):
        A.wb_credit(conn, "WIZ", 0, "installment", "PAD", S, created_by=BY)
    assert ledger_count(conn) == n
    # an overdraw by penalty through the wrapper is refused; penalty() is the only way
    with pytest.raises(RuleError, match="wb_spend can't write a 'penalty'"):
        A.wb_spend(conn, "WIZ", 500, "penalty", "IN_SEASON", S, created_by="zach")
    assert bal(conn, "WIZ") == 100
    A.penalty(conn, "WIZ", 500, rule="test", created_by="zach", season=S)
    assert bal(conn, "WIZ") == -400


def test_reversal_names_the_row_it_reverses(conn):
    fund(conn, "WIZ", 100)
    lid = A.admin_adjust(conn, reason="oops", created_by="zach", team="WIZ", amount=-30, season=S)["ledger_id"]
    with pytest.raises(RuleError, match="must name the ledger row"):
        A.admin_adjust(conn, reason="undo", created_by="zach", team="WIZ", amount=30, kind="reversal", season=S)
    with pytest.raises(RuleError, match="only for kind='reversal'"):
        A.admin_adjust(conn, reason="undo", created_by="zach", team="WIZ", amount=30, reverses_ledger_id=lid, season=S)
    with pytest.raises(RuleError, match="does not exist"):
        A.admin_adjust(conn, reason="undo", created_by="zach", amount=30, kind="reversal", reverses_ledger_id=10**9, season=S)
    with pytest.raises(RuleError, match="belongs to WIZ"):
        A.admin_adjust(conn, reason="undo", created_by="zach", team="HAM", amount=30, kind="reversal", reverses_ledger_id=lid, season=S)
    rid = A.admin_adjust(conn, reason="undo", created_by="zach", amount=30, kind="reversal", reverses_ledger_id=lid, season=S)["ledger_id"]
    assert conn.execute("select kind, ref_type, ref_id, amount from wb_ledger where id = %s", (rid,)).fetchone() == \
        ("reversal", "reversal", str(lid), 30)
    with pytest.raises(RuleError, match="already reversed"):
        A.admin_adjust(conn, reason="undo again", created_by="zach", amount=30, kind="reversal", reverses_ledger_id=lid, season=S)
    assert bal(conn, "WIZ") == 100


# ------------------------------------------------------- 3. retain_bc is idempotent

def test_retain_bc_second_call_does_not_charge_again(conn):
    fund(conn, "WIZ", 100)
    off = mk_prospect(conn, "bc-off", "WIZ", "BC")
    conn.execute("""insert into top100_snapshots (snapshot_date, rank, upid, name, official)
                    values ('2026-11-01', 9999, %s, 'x', true)""", (mk_prospect(conn, "bc-on", "WIZ", "BC"),))
    first = A.retain_bc(conn, off, season=S, choice="repurchase")
    assert first["charged"] == 20 and bal(conn, "WIZ") == 80
    n = ledger_count(conn)
    second = A.retain_bc(conn, off, season=S, choice="repurchase")
    assert second["charged"] == 0 and second["already_decided"] and bal(conn, "WIZ") == 80 and ledger_count(conn) == n
    assert A.retain_bc(conn, off, season=S, choice="DC")["charged"] == 0      # a different choice later changes nothing
    assert contract(conn, off)["ptype"] == "BC"
    assert conn.execute("select ref_type, ref_id from wb_ledger where upid = 'bc-off'").fetchall() == [("bc_retain", f"{S}:bc-off")]
    # a drop to DC is stored too, so the second call doesn't fail with "not a Blue Chip"
    dc = mk_prospect(conn, "bc-dc", "WIZ", "BC")
    assert A.retain_bc(conn, dc, season=S, choice="DC")["charged"] == 5
    again = A.retain_bc(conn, dc, season=S, choice="DC")
    assert again["charged"] == 0 and again["prospect_type"] == "DC" and bal(conn, "WIZ") == 75
    # the free retention is recorded as well
    assert A.retain_bc(conn, "bc-on", season=S)["retained"] is True
    assert A.retain_bc(conn, "bc-on", season=S)["already_decided"] is True
    # the database refuses a second charge row for the same player and season
    with pytest.raises(UNIQUE):
        with conn.transaction():
            raw_ledger(conn, "WIZ", -20, "prospect_contract", "bc_retain", f"{S}:bc-off")


# ------------------------------------------------------- 4. IL tag allowance

def test_il_tag_allowance_survives_release_and_trade(conn):
    fund(conn, "WIZ", 300)
    fund(conn, "HAM", 300)
    mk_keeper(conn, "il-a", "WIZ", "VC-2")
    mk_keeper(conn, "il-b", "WIZ", "VC-1")
    mk_keeper(conn, "il-c", "HAM", "VC-1")
    A.il_tag(conn, "il-a", created_by=BY, approved_by="zach", season=S)
    A.release(conn, "il-a", created_by=BY, season=S)
    with pytest.raises(RuleError, match="already used its VC IL tag"):      # the leak: a released player's tag still counts
        A.il_tag(conn, "il-b", created_by=BY, approved_by="zach", season=S)
    # a traded tagged player still uses the old team's allowance, not the new team's
    mk_keeper(conn, "il-d", "WIZ", "TC-2")
    mk_keeper(conn, "il-e", "WIZ", "TC-1")
    A.il_tag(conn, "il-d", created_by=BY, approved_by="zach", season=S)
    A.transfer(conn, "il-d", "WIZ", "HAM", created_by="trade-service", season=S)
    with pytest.raises(RuleError, match="already used its TC IL tag"):
        A.il_tag(conn, "il-e", created_by=BY, approved_by="zach", season=S)
    mk_keeper(conn, "il-f", "HAM", "TC-1")
    A.il_tag(conn, "il-f", created_by=BY, approved_by="zach", season=S)     # HAM has not used its TC tag
    A.il_tag(conn, "il-c", created_by=BY, approved_by="zach", season=S)     # nor its VC tag
    # next season the allowance is fresh
    A.il_tag(conn, "il-b", created_by=BY, approved_by="zach", season=S + 1)


def test_il_tag_sets_ineligible_until(conn):
    fund(conn, "WIZ", 300)
    mk_keeper(conn, "il-g", "WIZ", "VC-1")
    mk_keeper(conn, "il-h", "WIZ", "TC-1")
    A.open_window(conn, S, "KAP", utc(2027, 2, 1), utc(2027, 3, 1), "zach")
    A.il_tag(conn, "il-g", created_by=BY, approved_by="zach", season=S, now=utc(2027, 2, 20))
    got = conn.execute("select il_ineligible_until from contracts where upid = 'il-g'").fetchone()[0]
    assert got == date(2027, 2, 20) + timedelta(days=35)     # 2027 has no kap_end_date: the tag date + 35 days
    A.open_window(conn, 2026, "KAP", utc(2026, 1, 1), utc(2026, 12, 1), "zach")
    A.il_tag(conn, "il-h", created_by=BY, approved_by="zach", season=2026, now=utc(2026, 2, 20))
    got = conn.execute("select il_ineligible_until from contracts where upid = 'il-h'").fetchone()[0]
    assert got == date(2026, 3, 4) + timedelta(days=35)      # seasons.dates['kap_end_date'] + 5 weeks
    assert conn.execute("select il_ineligible_until from contracts where upid = 'il-h'").fetchone()[0] == date(2026, 4, 8)


# ------------------------------------------------------- 5. phase windows

def test_kap_actions_need_an_open_kap_window(conn):
    fund(conn, "WIZ", 500)
    for u, t in (("w-keep", "TC-1"), ("w-il", "VC-1"), ("w-rat", "FC-1")):
        mk_keeper(conn, u, "WIZ", t)
    A.close_window(conn, S, "KAP", "zach")
    n = ledger_count(conn)
    with pytest.raises(RuleError, match="KAP window"):
        A.keep_player(conn, "w-keep", created_by=BY, season=S)
    with pytest.raises(RuleError, match="KAP window"):
        A.il_tag(conn, "w-il", created_by=BY, approved_by="zach", season=S)
    with pytest.raises(RuleError, match="KAP window"):
        A.reduce_a_tier(conn, "w-rat", created_by=BY, season=S)
    assert ledger_count(conn) == n
    # a window that hasn't opened yet, or has already closed, is closed
    A.open_window(conn, S, "KAP", utc(2030, 1, 1), utc(2030, 2, 1), "zach")
    with pytest.raises(RuleError, match="KAP window"):
        A.keep_player(conn, "w-keep", created_by=BY, season=S)
    assert A.keep_player(conn, "w-keep", created_by=BY, season=S, now=utc(2030, 1, 15))["charged"] == 15
    with pytest.raises(RuleError, match="KAP window"):
        A.il_tag(conn, "w-il", created_by=BY, approved_by="zach", season=S, now=utc(2030, 2, 1))   # closes_at is exclusive
    A.close_window(conn, S, "KAP", "zach", now=utc(2030, 1, 20))
    with pytest.raises(RuleError, match="KAP window"):
        A.reduce_a_tier(conn, "w-rat", created_by=BY, season=S, now=utc(2030, 1, 21))


def test_pad_actions_need_an_open_pad_window(conn):
    fund(conn, "WIZ", 100)
    p = mk_player(conn, "w-pad")
    A.close_window(conn, S, "PAD", "zach")
    n = ledger_count(conn)
    with pytest.raises(RuleError, match="PAD window"):
        A.buy_draft_slot(conn, "WIZ", "BC", created_by=BY, season=S)
    with pytest.raises(RuleError, match="PAD window"):
        A.assign_prospect_contract(conn, p, "WIZ", "DC", acquired_via="pad", created_by=BY, season=S)
    assert ledger_count(conn) == n and contract(conn, p) is None
    # no window argument exists any more: the caller can't say "it is open"
    with pytest.raises(TypeError):
        A.buy_draft_slot(conn, "WIZ", "BC", created_by=BY, season=S, phase="PAD")
    with pytest.raises(TypeError):
        A.upgrade_prospect(conn, p, "BC", created_by=BY, season=S, finalization_open=True)
    A.open_window(conn, S, "PAD", utc(2020, 1, 1), utc(2090, 1, 1), "zach")
    A.buy_draft_slot(conn, "WIZ", "BC", created_by=BY, season=S)
    assert A.assign_prospect_contract(conn, p, "WIZ", "DC", acquired_via="pad", created_by=BY, season=S)["charged"] == 5


def test_bc_upgrade_needs_the_finalization_window_and_dc_to_pc_does_not(conn):
    fund(conn, "WIZ", 100)
    dc = mk_prospect(conn, "w-up", "WIZ", "DC")
    A.close_window(conn, S, "PAD", "zach")
    A.close_window(conn, S, "KAP", "zach")
    assert A.upgrade_prospect(conn, dc, "PC", created_by=BY, season=S)["charged"] == 5
    assert conn.execute("select phase from wb_ledger where upid = 'w-up'").fetchone()[0] == "IN_SEASON"
    with pytest.raises(RuleError, match="finalization"):
        A.upgrade_prospect(conn, dc, "BC", created_by=BY, season=S)
    A.open_window(conn, S, "BC_FINALIZATION", utc(2020, 1, 1), utc(2090, 1, 1), "zach")
    assert A.upgrade_prospect(conn, dc, "BC", created_by=BY, season=S)["charged"] == 10
    assert conn.execute("select phase from wb_ledger where upid = 'w-up' order by id desc").fetchone()[0] == "PAD"


def test_window_admin_functions_validate(conn):
    with pytest.raises(RuleError, match="must be one of"):
        A.open_window(conn, S, "SPRING", utc(2020, 1, 1), utc(2021, 1, 1), "zach")
    with pytest.raises(RuleError, match="close after it opens"):
        A.open_window(conn, S, "TRADE", utc(2021, 1, 1), utc(2020, 1, 1), "zach")
    with pytest.raises(RuleError, match="named admin"):
        A.open_window(conn, S, "TRADE", utc(2020, 1, 1), utc(2021, 1, 1), "system")
    with pytest.raises(RuleError, match="no TRADE window"):
        A.close_window(conn, S, "TRADE", "zach")


# ------------------------------------------------------- 8. missing tests from the review

def test_rollover_refuses_when_next_installment_already_credited(conn):
    fund(conn, "WIZ", 50)
    A.close_phase_rollover(conn, S, "PAD", "KAP", created_by="system")
    A.credit_installment(conn, S, "KAP", created_by="system")
    n = ledger_count(conn)
    A.close_phase_rollover(conn, S, "PAD", "KAP", created_by="system")    # teams already rolled over: still a no-op
    assert ledger_count(conn) == n
    # a rollover that has not run yet may not run after the installment it would be counted in
    A.credit_installment(conn, S, "APA", created_by="system")
    with pytest.raises(RuleError, match="already has its APA installment"):
        A.close_phase_rollover(conn, S, "KAP", "APA", created_by="system")
    assert ledger_count(conn) == n + conn.execute("select count(*) from teams where active").fetchone()[0]


def test_round_1_2_pick_needs_an_open_bc_slot(conn):
    fund(conn, "WIZ", 100)
    A.buy_draft_slot(conn, "WIZ", "DC", created_by=BY, season=S, count=3)    # DC slots can't be used for round 1-2
    for rnd in (1, 2):
        u = mk_player(conn, f"nobc-{rnd}")
        with pytest.raises(RuleError, match="no open BC draft slot"):
            A.assign_prospect_contract(conn, u, "WIZ", acquired_via="prospect_draft", draft_round=rnd, created_by=BY, season=S)
        assert contract(conn, u) is None
    A.buy_draft_slot(conn, "WIZ", "BC", created_by=BY, season=S)
    u = mk_player(conn, "nobc-ok")
    assert A.assign_prospect_contract(conn, u, "WIZ", acquired_via="prospect_draft", draft_round=1,
                                      created_by=BY, season=S)["prospect_type"] == "BC"
    with pytest.raises(RuleError, match="no open BC draft slot"):
        A.assign_prospect_contract(conn, mk_player(conn, "nobc-extra"), "WIZ", acquired_via="prospect_draft",
                                   draft_round=2, created_by=BY, season=S)
