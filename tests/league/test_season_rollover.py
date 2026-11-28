"""The season-boundary rollover (Constitution Art. I s02.2, Art. IV s02.3.1; migration 0011).

Up to $25 of last season's leftover WizBucks into each of PAD, KAP and APA, split the way the
manager declares. Needs the local Postgres via DATABASE_URL; every test rolls back.
"""

import psycopg
import pytest

from league import actions as A
from league import jobs
from league.actions import RuleError
from tests.league.test_actions import BY, S, bal, fund, ledger_count, utc


def rows(conn, team):
    with conn.cursor() as cur:
        cur.execute("""select kind, amount, ref_id from wb_ledger
                        where team_abbr = %s and ref_type = 'rollover' order by id""", (team,))
        return cur.fetchall()


def test_declared_split_is_carried_and_the_rest_forfeited(conn):
    fund(conn, "WAR", 90)
    r = A.declare_season_rollover(conn, "WAR", {"PAD": 25, "KAP": 25, "APA": 20}, created_by="WAR", season=S)
    assert r["total"] == 70 and r["forfeit"] == 20
    assert bal(conn, "WAR") == 90                       # declaring moves nothing

    res = A.apply_season_rollover(conn, S)
    war = next(t for t in res["teams"] if t["team"] == "WAR")
    assert war["carried"] == {"PAD": 25, "KAP": 25, "APA": 20} and war["forfeited"] == 20
    # only the PAD share is spendable during PAD
    assert bal(conn, "WAR") == 25
    assert rows(conn, "WAR") == [
        ("rollover_forfeit", -20, f"{S}:PRIOR_SEASON"),
        ("rollover_hold", -25, f"{S}:PRIOR_SEASON>KAP"),
        ("rollover_hold", -20, f"{S}:PRIOR_SEASON>APA"),
        ("rollover_in", 0, f"{S}:PRIOR_SEASON>PAD"),
    ]

    # the KAP and APA shares come back with their installments, once
    A.credit_installment(conn, S, "PAD")
    A.close_phase_rollover(conn, S, "PAD", "KAP")       # PAD money over $30 would be forfeited here; the held $45 is not in it
    before = bal(conn, "WAR")
    kap = next(t for t in A.credit_installment(conn, S, "KAP")["teams"] if t["team"] == "WAR")
    assert kap["rollover"] == 25 and bal(conn, "WAR") == before + kap["amount"] + kap["reward"] + 25
    n = ledger_count(conn)
    A.credit_installment(conn, S, "KAP")
    assert ledger_count(conn) == n
    A.close_phase_rollover(conn, S, "KAP", "APA")
    before = bal(conn, "WAR")
    apa = next(t for t in A.credit_installment(conn, S, "APA")["teams"] if t["team"] == "WAR")
    assert apa["rollover"] == 20 and bal(conn, "WAR") == before + apa["amount"] + 20


def test_declaration_limits(conn):
    fund(conn, "WAR", 40)
    with pytest.raises(RuleError, match=r"at most \$25 can roll over into KAP"):
        A.declare_season_rollover(conn, "WAR", {"KAP": 30}, created_by="WAR", season=S)
    with pytest.raises(RuleError, match=r"has \$40 left over and can't carry \$50"):
        A.declare_season_rollover(conn, "WAR", {"PAD": 25, "KAP": 25}, created_by="WAR", season=S)
    with pytest.raises(RuleError, match="not PTDA"):
        A.declare_season_rollover(conn, "WAR", {"PTDA": 5}, created_by="WAR", season=S)
    with pytest.raises(RuleError, match="whole number"):
        A.declare_season_rollover(conn, "WAR", {"PAD": -5}, created_by="WAR", season=S)
    # the cap is a database rule too
    with pytest.raises(psycopg.errors.RaiseException, match="at most"), conn.transaction():
        conn.execute("""insert into wb_rollover_declarations (season, team_abbr, to_phase, amount, declared_by)
                        values (%s, 'WAR', 'APA', 26, 'x')""", (S,))

    # a manager can change his mind until the rollover is applied
    A.declare_season_rollover(conn, "WAR", {"PAD": 25, "KAP": 15}, created_by="WAR", season=S)
    r = A.declare_season_rollover(conn, "WAR", {"PAD": 15, "KAP": 25}, created_by="WAR", season=S)
    assert r["declared"] == {"PAD": 15, "KAP": 25, "APA": 0} and r["forfeit"] == 0
    A.apply_season_rollover(conn, S)
    assert bal(conn, "WAR") == 15
    with pytest.raises(RuleError, match="already been applied"):
        A.declare_season_rollover(conn, "WAR", {"PAD": 25}, created_by="WAR", season=S)


def test_nothing_declared_carries_the_most_the_caps_allow(conn):
    fund(conn, "WAR", 60)       # under $75: PAD and KAP fill, APA gets what is left
    fund(conn, "HAM", 100)      # over $75: $25 each and $25 forfeited
    res = {t["team"]: t for t in A.apply_season_rollover(conn, S)["teams"]}
    assert res["WAR"]["carried"] == {"PAD": 25, "KAP": 25, "APA": 10} and res["WAR"]["forfeited"] == 0
    assert res["HAM"]["carried"] == {"PAD": 25, "KAP": 25, "APA": 25} and res["HAM"]["forfeited"] == 25
    assert not res["WAR"]["declared"]
    assert bal(conn, "WAR") == 25 and bal(conn, "HAM") == 25
    assert res["SAD"]["carried"] == {"PAD": 0, "KAP": 0, "APA": 0}      # a team with nothing left over


def test_a_declaration_larger_than_the_money_left_is_honoured_pad_first(conn):
    fund(conn, "WAR", 60)
    A.declare_season_rollover(conn, "WAR", {"PAD": 20, "KAP": 20, "APA": 20}, created_by="WAR", season=S)
    A.wb_spend(conn, "WAR", 30, "trade_out", "OFFSEASON", S, created_by=BY)      # an offseason trade after declaring
    war = next(t for t in A.apply_season_rollover(conn, S)["teams"] if t["team"] == "WAR")
    assert war["carried"] == {"PAD": 20, "KAP": 10, "APA": 0} and war["forfeited"] == 0


def test_apply_is_safe_to_run_twice(conn):
    fund(conn, "WAR", 30)
    A.apply_season_rollover(conn, S)
    n, b = ledger_count(conn), bal(conn, "WAR")
    assert A.apply_season_rollover(conn, S)["teams"] == []
    assert ledger_count(conn) == n and bal(conn, "WAR") == b


def test_apply_refuses_after_the_pad_installment(conn):
    A.credit_installment(conn, S, "PAD")
    with pytest.raises(RuleError, match="season rollover must run before"):
        A.apply_season_rollover(conn, S)


def test_the_installment_job_applies_the_rollover_when_pad_opens(conn):
    fund(conn, "WAR", 90)
    A.declare_season_rollover(conn, "WAR", {"PAD": 25}, created_by="WAR", season=S)
    out = jobs.run_installments(conn, {"season": S, "now": utc(2027, 5, 10, 16)})   # the fixture has PAD open
    assert "season rollover" in out
    assert rows(conn, "WAR")[0] == ("rollover_forfeit", -65, f"{S}:PRIOR_SEASON")
    pad = conn.execute("""select amount from wb_ledger where team_abbr = 'WAR' and kind = 'installment'
                           and ref_id = %s""", (f"{S}:PAD",)).fetchone()[0]
    reward = conn.execute("""select coalesce(sum(amount), 0) from wb_ledger where team_abbr = 'WAR'
                              and kind = 'finish_reward' and ref_id = %s""", (f"{S}:PAD",)).fetchone()[0]
    assert bal(conn, "WAR") == 25 + pad + reward
