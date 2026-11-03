from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from auction.rules import (
    Bid, BidError, Phase, Prospect, committed, high_bid, phase_for, resolve, validate_bid,
)

ET = ZoneInfo("America/New_York")
# Seeds: index 0 = worst standings = best priority.
ORDER = ["AAA", "BBB", "CCC", "DDD", "EEE"]
CAL = {"start": "2027-03-08", "all_star_break_start": "2027-07-12",
       "restart": "2027-07-19", "playoffs_start": "2027-11-29"}

# Auction week of Mon 2027-04-05 (no DST change inside it).
MON = datetime(2027, 4, 5, tzinfo=ET)


def at(day_offset, h, m=0, base=MON):
    d = base + timedelta(days=day_offset)
    return d.replace(hour=h, minute=m)


def name(dt, cal=CAL):
    return phase_for(dt, cal).name


def bid(team, upid, kind, amount, when):
    return Bid(team, upid, kind, amount, when)


def check(b, bids, balance=500, now=None, prospect=None, order=ORDER):
    now = now or b.placed_at
    validate_bid(b, phase_for(now, CAL), bids, balance, order, now, prospect)


def err(b, bids, **kw):
    with pytest.raises(BidError) as e:
        check(b, bids, **kw)
    return e.value.message


OB1 = bid("CCC", "p1", "OB", 10, at(0, 16))


# ---- phases -------------------------------------------------------------

def test_phase_boundaries_monday_3pm():
    assert name(at(0, 14, 59)) == "processing"
    assert phase_for(at(0, 14, 59), CAL).week_start == date(2027, 3, 29)
    assert name(at(0, 15)) == "ob_window"
    assert phase_for(at(0, 15), CAL).week_start == date(2027, 4, 5)


def test_phase_ob_window_ends_wednesday_midnight():
    assert name(at(1, 23, 59)) == "ob_window"
    p = phase_for(at(2, 0), CAL)
    assert p.name == "cb_window"
    assert phase_for(at(1, 12), CAL).phase_ends_at == at(2, 0)


def test_phase_friday_9pm():
    assert name(at(4, 20, 59)) == "cb_window"
    assert name(at(4, 21)) == "off_week"  # gap in the spec's table, see report
    assert phase_for(at(4, 12), CAL).phase_ends_at == at(4, 21)
    assert name(at(5, 0)) == "ob_final"


def test_phase_sunday_10am():
    assert name(at(6, 9, 59)) == "ob_final"
    assert phase_for(at(6, 9, 59), CAL).phase_ends_at == at(6, 10)
    assert name(at(6, 10)) == "processing"
    assert phase_for(at(6, 10), CAL).phase_ends_at == at(7, 15)


def test_phase_accepts_any_timezone():
    utc = at(0, 15).astimezone(timezone.utc)
    assert name(utc) == "ob_window"
    assert name(utc - timedelta(minutes=1)) == "processing"


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        phase_for(datetime(2027, 4, 5, 16), CAL)


def test_dst_start_march_2027():
    # DST begins Sun 2027-03-14 at 2am. Week of Mon 3/8; Sunday 10am is EDT.
    sat = datetime(2027, 3, 14, 9, 59, tzinfo=ET).astimezone(timezone.utc)
    assert sat.hour == 13  # 9:59 EDT = 13:59 UTC (EST would be 14:59)
    assert name(sat) == "ob_final"
    assert name(sat + timedelta(minutes=1)) == "processing"
    # Monday 3pm after the change is EDT too
    assert name(datetime(2027, 3, 15, 14, 59, tzinfo=ET).astimezone(timezone.utc)) == "processing"
    assert name(datetime(2027, 3, 15, 15, 0, tzinfo=ET).astimezone(timezone.utc)) == "ob_window"
    # Monday 3pm before the change was EST: 15:00 EST = 20:00 UTC
    assert name(datetime(2027, 3, 8, 20, 0, tzinfo=timezone.utc)) == "ob_window"
    assert name(datetime(2027, 3, 8, 19, 59, tzinfo=timezone.utc)) == "off_week"  # week before start


def test_dst_end_november_2027():
    # DST ends Sun 2027-11-07. Week of Mon 11/1 has EDT early and EST on Sunday.
    # Sun 10:00 EST = 15:00 UTC; 9:59 EST = 14:59 UTC.
    assert name(datetime(2027, 11, 7, 14, 59, tzinfo=timezone.utc)) == "ob_final"
    assert name(datetime(2027, 11, 7, 15, 0, tzinfo=timezone.utc)) == "processing"
    # Mon 11/8 3pm EST = 20:00 UTC
    assert name(datetime(2027, 11, 8, 19, 59, tzinfo=timezone.utc)) == "processing"
    assert name(datetime(2027, 11, 8, 20, 0, tzinfo=timezone.utc)) == "ob_window"
    # phase_ends_at carries the right offset: Wed 00:00 is EST, 05:00 UTC
    p = phase_for(datetime(2027, 11, 8, 20, 0, tzinfo=timezone.utc), CAL)
    assert p.phase_ends_at.astimezone(timezone.utc).hour == 5


def test_before_season_start_is_off_week():
    p = phase_for(datetime(2027, 3, 2, 12, tzinfo=ET), CAL)
    assert p.name == "off_week" and p.week_start == date(2027, 3, 1)
    assert p.phase_ends_at == datetime(2027, 3, 8, 15, tzinfo=ET)


def test_all_star_break_is_off_week():
    # Week of 7/5 is active; Sunday 7/11 processes; weeks of 7/12 off; 7/19 restarts.
    assert name(datetime(2027, 7, 8, 12, tzinfo=ET)) == "cb_window"
    assert name(datetime(2027, 7, 11, 11, tzinfo=ET)) == "processing"
    assert name(datetime(2027, 7, 12, 14, tzinfo=ET)) == "processing"  # still last week's
    assert name(datetime(2027, 7, 12, 16, tzinfo=ET)) == "off_week"
    assert name(datetime(2027, 7, 15, 12, tzinfo=ET)) == "off_week"
    p = phase_for(datetime(2027, 7, 15, 12, tzinfo=ET), CAL)
    assert p.phase_ends_at == datetime(2027, 7, 19, 15, tzinfo=ET)
    assert name(datetime(2027, 7, 19, 14, tzinfo=ET)) == "off_week"  # prev week was break
    assert name(datetime(2027, 7, 19, 15, tzinfo=ET)) == "ob_window"


def test_playoffs_onward_is_off_week():
    assert name(datetime(2027, 11, 28, 11, tzinfo=ET)) == "processing"
    assert name(datetime(2027, 11, 29, 14, tzinfo=ET)) == "processing"
    p = phase_for(datetime(2027, 11, 29, 15, tzinfo=ET), CAL)
    assert p.name == "off_week" and p.phase_ends_at is None
    assert name(datetime(2028, 1, 5, tzinfo=ET)) == "off_week"


# ---- helpers ------------------------------------------------------------

def test_high_bid_and_committed():
    bids = [OB1, bid("AAA", "p1", "CB", 20, at(2, 9)), bid("BBB", "p2", "OB", 12, at(0, 17))]
    assert high_bid("p1", bids, ORDER) == ("AAA", 20)
    assert high_bid("nope", bids, ORDER) is None
    assert committed("AAA", bids, ORDER) == 20
    assert committed("CCC", bids, ORDER) == 0  # outbid, nothing held
    assert committed("BBB", bids, ORDER) == 12


def test_no_ties_equal_amount_goes_to_lower_seed():
    bids = [OB1, bid("BBB", "p1", "CB", 20, at(2, 9)), bid("AAA", "p1", "CB", 20, at(2, 10))]
    assert high_bid("p1", bids, ORDER) == ("AAA", 20)
    assert committed("AAA", bids, ORDER) == 20
    assert committed("BBB", bids, ORDER) == 0  # only one team has the amount held


# ---- OB rules -----------------------------------------------------------

def test_ob_ok():
    check(OB1, [])


def test_ob_minimum():
    assert err(bid("CCC", "p1", "OB", 9, at(0, 16)), []) == "Originating bids must be at least $10 WB."


def test_ob_one_per_team():
    assert err(bid("CCC", "p2", "OB", 10, at(1, 9)), [OB1]) == \
        "You have already placed an originating bid this week."


def test_ob_one_per_prospect():
    assert err(bid("DDD", "p1", "OB", 10, at(1, 9)), [OB1]) == "This prospect already has an originating bid."


def test_ob_wrong_phase():
    assert "Originating bids are only allowed" in err(bid("CCC", "p1", "OB", 10, at(2, 9)), [])


def test_prospect_must_be_eligible():
    p = Prospect("p1", False, "Prospect already owned and not eligible for auction.")
    assert err(OB1, [], prospect=p) == "Prospect already owned and not eligible for auction."
    assert err(OB1, [], prospect=Prospect("p1", False)) == "Prospect not found or not eligible."


def test_unknown_team_and_bad_inputs():
    assert err(bid("ZZZ", "p1", "OB", 10, at(0, 16)), []) == "Unknown team: ZZZ"
    assert err(bid("CCC", "p1", "XX", 10, at(0, 16)), []) == "Invalid bid type. Use OB or CB."
    assert err(bid("CCC", "p1", "OB", 0, at(0, 16)), []) == "Bid amount must be positive."


def test_phase_gate_off_and_processing():
    assert err(bid("CCC", "p1", "OB", 10, at(6, 11)), []) == "Auction is in processing; bids are closed."
    assert err(bid("CCC", "p1", "OB", 10, datetime(2027, 3, 1, 12, tzinfo=ET)), []) == \
        "Auctions are not active right now."
    assert "Originating bids are only allowed" in err(bid("CCC", "p1", "OB", 10, at(5, 12)), [])


# ---- CB rules -----------------------------------------------------------

def cb(team, amount, day=2, h=9, m=0, upid="p1"):
    return bid(team, upid, "CB", amount, at(day, h, m))


def test_cb_ok_and_phase():
    check(cb("AAA", 15), [OB1])
    assert "Challenge bids are only allowed" in err(cb("AAA", 15, day=1), [OB1])


def test_cb_requires_ob():
    assert err(cb("AAA", 15), []) == "Challenge bids require an existing originating bid."


def test_ob_team_cannot_cb_own():
    assert err(cb("CCC", 15), [OB1]) == "Originating manager cannot place challenge bids on their own OB."


def test_cb_raise_must_be_5_above():
    assert "at least $5 above the current high ($10)" in err(cb("AAA", 14), [OB1])
    check(cb("AAA", 15), [OB1])


def test_cb_equal_with_better_priority_allowed():
    bids = [OB1, cb("DDD", 20)]
    check(cb("AAA", 20, h=10), bids)  # AAA seed 0 beats DDD seed 3


def test_cb_equal_lower_seed_holds_so_worse_priority_rejected():
    bids = [OB1, cb("AAA", 20)]
    msg = err(cb("DDD", 20, h=10), bids)
    assert "To priority-match at $20" in msg and "(AAA)" in msg


def test_cb_equal_to_ob_amount_by_priority():
    # OB team CCC (seed 2) holds $10; AAA (seed 0) can match at $10, DDD (seed 3) cannot.
    check(cb("AAA", 10), [OB1])
    assert "To priority-match" in err(cb("DDD", 10), [OB1])


def test_cb_one_per_team_per_day_when_outbid():
    bids = [OB1, cb("AAA", 15), cb("BBB", 20, h=10)]
    assert err(cb("AAA", 25, h=11), bids) == "You already have a challenge bid on this prospect today."
    # next ET day is fine
    check(cb("AAA", 25, day=3), bids)


def test_cb_same_day_raise_allowed_for_high_holder():
    bids = [OB1, cb("AAA", 15)]
    check(cb("AAA", 20, h=11), bids)


def test_cb_day_uses_et_calendar_day():
    # 11:30pm ET Wednesday is already Thursday in UTC; still the same ET day as 9am Wed.
    late = at(2, 23, 30).astimezone(timezone.utc)
    bids = [OB1, cb("AAA", 15), cb("BBB", 20, h=10)]
    b = Bid("AAA", "p1", "CB", 25, late)
    assert err(b, bids) == "You already have a challenge bid on this prospect today."


def test_friday_spoiler_rule():
    bids = [OB1, cb("AAA", 15)]
    assert err(cb("BBB", 20, day=4, h=12), bids).startswith("Friday CBs require at least one prior CB")
    check(cb("AAA", 20, day=4, h=12), bids)  # AAA has an earlier CB, high holder raising... 
    # a team with an earlier-week CB who was outbid may counter on Friday
    bids = [OB1, cb("AAA", 15), cb("BBB", 20, day=3)]
    check(cb("AAA", 25, day=4, h=12), bids)


def test_friday_after_9pm_closed():
    assert name(at(4, 21)) != "cb_window"
    assert err(cb("AAA", 20, day=4, h=21), [OB1]) == "Auctions are not active right now."


# ---- WizBucks -----------------------------------------------------------

def test_insufficient_wb():
    msg = err(cb("AAA", 60), [OB1], balance=50)
    assert msg == "Insufficient WB. You have $50 available (total $50, committed $0)."


def test_committed_reduces_available():
    # AAA already holds $40 on p2; balance 50 leaves $10.
    bids = [OB1, bid("BBB", "p2", "OB", 10, at(0, 17)), bid("AAA", "p2", "CB", 40, at(2, 8))]
    msg = err(cb("AAA", 15), bids, balance=50)
    assert msg == "Insufficient WB. You have $10 available (total $50, committed $40)."


def test_raising_own_high_frees_old_amount():
    bids = [OB1, cb("AAA", 40)]
    check(cb("AAA", 50, h=11), bids, balance=50)  # available 50-40+40 = 50
    assert "Insufficient WB" in err(cb("AAA", 55, h=11), bids, balance=50)


def test_outbid_amount_not_committed():
    bids = [OB1, cb("AAA", 40), cb("BBB", 45, h=10)]
    check(bid("AAA", "p2", "CB", 50, at(3, 9)), bids + [bid("DDD", "p2", "OB", 10, at(0, 18))], balance=50)


# ---- resolution ---------------------------------------------------------

def ob(team, upid, amount, h=16):
    return bid(team, upid, "OB", amount, at(0, h))


def test_ob_only_pays_ob_amount():
    r = resolve([ob("CCC", "p1", 25)], {}, {"CCC": 100}, ORDER)
    assert [(x.upid, x.team, x.amount, x.outcome) for x in r] == [("p1", "CCC", 25, "OB_ONLY")]
    r = resolve([ob("CCC", "p1", 10)], {}, {"CCC": 100}, ORDER)
    assert r[0].amount == 10


def test_match_ob_team_wins_at_top_cb():
    bids = [ob("CCC", "p1", 10), cb("AAA", 30), cb("BBB", 40, h=10)]
    r = resolve(bids, {"p1": "match"}, {"CCC": 100, "AAA": 100, "BBB": 100}, ORDER)
    assert (r[0].team, r[0].amount, r[0].outcome) == ("CCC", 40, "OB_MATCH")


def test_forfeit_and_no_decision_top_cb_wins():
    bids = [ob("CCC", "p1", 10), cb("AAA", 30), cb("BBB", 40, h=10)]
    bal = {"CCC": 100, "AAA": 100, "BBB": 100}
    for dec in ({"p1": "forfeit"}, {}):
        r = resolve(bids, dec, bal, ORDER)
        assert (r[0].team, r[0].amount, r[0].outcome) == ("BBB", 40, "CB_WIN")


def test_resolution_tie_goes_to_lower_seed():
    bids = [ob("CCC", "p1", 10), cb("DDD", 30), cb("BBB", 30, h=10)]
    r = resolve(bids, {}, {"DDD": 99, "BBB": 99, "CCC": 99}, ORDER)
    assert r[0].team == "BBB"


def test_affordability_drops_latest_win_first():
    bids = [ob("AAA", "p1", 30, 16), ob("BBB", "p2", 10, 17)]
    # AAA wins p1 at 30; BBB wins p2. Give AAA a second OB-win via CB on p2? Keep simple:
    bids += [bid("AAA", "p2", "CB", 40, at(2, 9))]
    r = resolve(bids, {}, {"AAA": 50, "BBB": 50}, ORDER)
    # AAA wins p1 (30) and p2 (40) = 70 > 50; latest win (p2 CB) is dropped, p2 goes to BBB at OB 10.
    got = {x.upid: (x.team, x.amount, x.outcome, x.dropped) for x in r}
    assert got["p1"] == ("AAA", 30, "OB_ONLY", [])
    assert got["p2"] == ("BBB", 10, "OB_ONLY", ["AAA"])


def test_affordability_cascade_rechecks_inheriting_team():
    # Spec test 6. A wins p1,p2,p3 and can't afford them; the dropped prospect lands on B,
    # which then goes over budget too and is trimmed.
    bids = [
        ob("BBB", "q", 10, 15),                 # B's own OB (B has one OB per week)
        ob("CCC", "p1", 10, 16), ob("DDD", "p2", 10, 17), ob("EEE", "p3", 10, 18),
        bid("AAA", "p1", "CB", 20, at(2, 9)),
        bid("AAA", "p2", "CB", 20, at(2, 10)),
        bid("AAA", "p3", "CB", 20, at(2, 11)),
        bid("BBB", "p3", "CB", 15, at(2, 12)),  # B is the runner-up on p3
    ]
    # A: 60 owed, balance 50 -> drop latest (p3). p3 goes to B at 15; B owes q 10 + p3 15 = 25 > 20.
    bal = {"AAA": 50, "BBB": 20, "CCC": 99, "DDD": 99, "EEE": 99}
    r = {x.upid: x for x in resolve(bids, {}, bal, ORDER)}
    assert (r["p1"].team, r["p1"].amount) == ("AAA", 20)
    assert (r["p2"].team, r["p2"].amount) == ("AAA", 20)
    # p3: A dropped, B dropped, then OB team EEE keeps it at its OB amount
    assert r["p3"].team == "EEE" and r["p3"].amount == 10 and r["p3"].outcome == "OB_ONLY"
    assert r["p3"].dropped == ["AAA", "BBB"]
    assert (r["q"].team, r["q"].amount) == ("BBB", 10)
    # every team within budget
    spend = {}
    for x in r.values():
        spend[x.team] = spend.get(x.team, 0) + x.amount
    assert all(spend[t] <= bal[t] for t in spend)


def test_affordability_unsold_when_nobody_can_pay():
    r = resolve([ob("CCC", "p1", 50)], {}, {"CCC": 20}, ORDER)
    assert r == []


def test_resolve_empty():
    assert resolve([], {}, {}, ORDER) == []
