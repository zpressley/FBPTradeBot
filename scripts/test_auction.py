"""Test script for the FBP Prospect Auction system.

Simulates a full auction week lifecycle using the first real auction week
(Mar 16–22, 2026):

  Mon 3:30pm  → OB bids
  Tue 6:00am–Fri 9:00pm → CB bids
  Sat noon    → Match / Forfeit decisions
  Sun 2pm     → resolve_week()

Uses a temporary data directory so production files are never touched.

Run:
    python scripts/test_auction.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Ensure repo root is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import wb_ledger
from auction_manager import AuctionManager, AuctionPhase, ET

# ── Simulated timestamps (first auction week: Mar 16–22 2026) ────────

MON_3PM  = datetime(2026, 3, 16, 15, 30, tzinfo=ET)
TUE_5AM  = datetime(2026, 3, 17, 5, 0,  tzinfo=ET)
TUE_6AM  = datetime(2026, 3, 17, 6, 0,  tzinfo=ET)
TUE_NOON = datetime(2026, 3, 17, 12, 0,  tzinfo=ET)
WED_NOON = datetime(2026, 3, 18, 12, 0,  tzinfo=ET)
THU_NOON = datetime(2026, 3, 19, 12, 0,  tzinfo=ET)
FRI_NOON = datetime(2026, 3, 20, 12, 0,  tzinfo=ET)
SAT_NOON = datetime(2026, 3, 21, 12, 0,  tzinfo=ET)
SUN_2PM  = datetime(2026, 3, 22, 14, 0,  tzinfo=ET)

# ── Minimal test fixtures ────────────────────────────────────────────

TEST_PLAYERS = [
    # Owned roster players (so _is_team_known() passes)
    {"upid": "1001", "id": "1001", "name": "Mike Trout",    "manager": "HAM", "player_type": "Active"},
    {"upid": "1002", "id": "1002", "name": "Shohei Ohtani", "manager": "RV",  "player_type": "Active"},
    {"upid": "1003", "id": "1003", "name": "Juan Soto",     "manager": "CFL", "player_type": "Active"},
    # Unowned prospects eligible for auction
    {"upid": "P001", "id": "P001", "name": "Ethan Salas",   "manager": None, "player_type": "Farm"},
    {"upid": "P002", "id": "P002", "name": "Roman Anthony",  "manager": None, "player_type": "Farm"},
    {"upid": "P003", "id": "P003", "name": "Upper Echelon",  "manager": None, "player_type": "Farm"},
]

TEST_WIZBUCKS = {
    "Hammers": 130,
    "Rick Vaughn": 145,
    "Country Fried Lamb": 125,
}

TEST_STANDINGS = {
    "standings": [
        {"team": "HAM", "rank": 9},
        {"team": "RV",  "rank": 10},
        {"team": "CFL", "rank": 8},
    ]
}

TEST_SEASON_DATES = {
    "auction": {
        "start": "2026-03-16",
        "all_star_break_start": "2026-07-13",
        "restart": "2026-07-20",
        "playoffs_start": "2026-09-07",
    }
}

TEST_MANAGERS = {
    "teams": {
        "HAM": {"name": "Hammers",            "final_rank_2025": 9},
        "RV":  {"name": "Rick Vaughn",         "final_rank_2025": 10},
        "CFL": {"name": "Country Fried Lamb",  "final_rank_2025": 8},
    }
}

# ── Helpers ───────────────────────────────────────────────────────────

passed = 0
failed = 0


def check(label, actual, expected):
    global passed, failed
    ok = actual == expected
    icon = "✅" if ok else "❌"
    if ok:
        print(f"  {icon} {label}: {actual!r}")
        passed += 1
    else:
        print(f"  {icon} {label}: expected {expected!r}, got {actual!r}")
        failed += 1


def check_err_contains(label, result, substring):
    """Check that a result is failure and error contains substring."""
    global passed, failed
    if not result.get("success") and substring.lower() in result.get("error", "").lower():
        print(f"  ✅ {label}: rejected — {result['error']}")
        passed += 1
    else:
        print(f"  ❌ {label}: expected rejection containing '{substring}', got {result}")
        failed += 1


def setup_test_dir():
    """Create temp directory tree with test fixtures. Returns (tmp_root, data_dir)."""
    tmp = Path(tempfile.mkdtemp(prefix="auc_test_"))
    data_dir = tmp / "data"
    config_dir = tmp / "config"
    data_dir.mkdir()
    config_dir.mkdir()

    (data_dir / "combined_players.json").write_text(json.dumps(TEST_PLAYERS, indent=2))
    (data_dir / "wizbucks.json").write_text(json.dumps(TEST_WIZBUCKS, indent=2))
    (data_dir / "standings.json").write_text(json.dumps(TEST_STANDINGS, indent=2))
    (data_dir / "wizbucks_transactions.json").write_text("[]")

    (config_dir / "season_dates.json").write_text(json.dumps(TEST_SEASON_DATES, indent=2))
    (config_dir / "managers.json").write_text(json.dumps(TEST_MANAGERS, indent=2))

    return tmp, data_dir


def patch_wb_ledger(tmp_dir):
    """Point wb_ledger at the temp directory so it doesn't touch real data."""
    wb_ledger.LEDGER_PATH = str(tmp_dir / "data" / "wizbucks_transactions.json")
    wb_ledger.WALLET_PATH = str(tmp_dir / "data" / "wizbucks.json")
    wb_ledger.MANAGERS_PATH = str(tmp_dir / "config" / "managers.json")


# ── Main ──────────────────────────────────────────────────────────────

def main():
    global passed, failed

    print("=" * 60)
    print("FBP Auction Test — Full Week Lifecycle")
    print("Simulated week: Mar 16–22, 2026")
    print("=" * 60)

    tmp_dir, data_dir = setup_test_dir()
    patch_wb_ledger(tmp_dir)
    am = AuctionManager(data_dir=data_dir)

    # ── 1. Phase detection ────────────────────────────────
    print("\n── Phase Detection ──")

    pre = datetime(2026, 3, 10, 12, 0, tzinfo=ET)
    check("Pre-season (Mar 10)",   am.get_current_phase(pre),      AuctionPhase.OFF_WEEK)
    check("Mon 3:30pm ET",         am.get_current_phase(MON_3PM),  AuctionPhase.OB_WINDOW)
    check("Tue 5:00am ET",         am.get_current_phase(TUE_5AM),  AuctionPhase.OB_WINDOW)
    check("Tue 6:00am ET",         am.get_current_phase(TUE_6AM),  AuctionPhase.CB_WINDOW)
    check("Tue noon ET",           am.get_current_phase(TUE_NOON), AuctionPhase.CB_WINDOW)
    check("Wed noon ET",           am.get_current_phase(WED_NOON), AuctionPhase.CB_WINDOW)
    check("Thu noon ET",           am.get_current_phase(THU_NOON), AuctionPhase.CB_WINDOW)
    check("Fri noon ET",           am.get_current_phase(FRI_NOON), AuctionPhase.CB_WINDOW)
    check("Sat noon ET",           am.get_current_phase(SAT_NOON), AuctionPhase.OB_FINAL)
    check("Sun 2pm ET",            am.get_current_phase(SUN_2PM),  AuctionPhase.PROCESSING)

    # ── 2. OB Bidding (Monday) ────────────────────────────
    print("\n── OB Bidding (Monday 3:30pm) ──")

    r = am.place_bid(team="HAM", prospect_id="P001", amount=20, bid_type="OB", now=MON_3PM)
    check("HAM OB $20 on Ethan Salas", r["success"], True)

    r = am.place_bid(team="HAM", prospect_id="P002", amount=15, bid_type="OB", now=MON_3PM)
    check_err_contains("HAM 2nd OB (1/team/week)", r, "already placed")

    r = am.place_bid(team="RV", prospect_id="P002", amount=10, bid_type="OB", now=MON_3PM)
    check("RV OB $10 on Roman Anthony", r["success"], True)

    r = am.place_bid(team="CFL", prospect_id="P001", amount=15, bid_type="OB", now=MON_3PM)
    check_err_contains("CFL OB on prospect with existing OB", r, "already has")

    r = am.place_bid(team="CFL", prospect_id="P003", amount=5, bid_type="OB", now=MON_3PM)
    check_err_contains("CFL OB $5 (below $10 min)", r, "at least $10")

    r = am.place_bid(team="CFL", prospect_id="P003", amount=10, bid_type="OB", now=MON_3PM)
    check("CFL OB $10 on Upper Echelon", r["success"], True)

    # Wrong bid type for the phase
    r = am.place_bid(team="RV", prospect_id="P001", amount=25, bid_type="CB", now=MON_3PM)
    check_err_contains("CB during OB window", r, "challenge bids are only")

    # ── 3. CB Bidding (Wed–Fri) ───────────────────────────
    print("\n── CB Bidding (Wed–Fri) ──")

    # OB not allowed in CB window
    r = am.place_bid(team="RV", prospect_id="P003", amount=10, bid_type="OB", now=WED_NOON)
    check_err_contains("OB during CB window", r, "originating bids are only")

    # RV challenges HAM's OB on P001: min is $20 + $5 = $25
    r = am.place_bid(team="RV", prospect_id="P001", amount=25, bid_type="CB", now=WED_NOON)
    check("RV CB $25 on P001 (Wed)", r["success"], True)

    # Same team, same prospect, same day → rejected
    r = am.place_bid(team="RV", prospect_id="P001", amount=30, bid_type="CB", now=WED_NOON)
    check_err_contains("RV 2nd CB same day", r, "already have a challenge bid")

    # Next day is fine
    r = am.place_bid(team="RV", prospect_id="P001", amount=30, bid_type="CB", now=THU_NOON)
    check("RV CB $30 on P001 (Thu)", r["success"], True)

    # CFL tries below min raise: high is $30, need >= $35
    r = am.place_bid(team="CFL", prospect_id="P001", amount=33, bid_type="CB", now=THU_NOON)
    check_err_contains("CFL CB $33 (below +$5 min raise)", r, "at least $5 above")

    # CFL valid raise
    r = am.place_bid(team="CFL", prospect_id="P001", amount=35, bid_type="CB", now=THU_NOON)
    check("CFL CB $35 on P001 (Thu)", r["success"], True)

    # OB manager cannot CB own prospect
    r = am.place_bid(team="HAM", prospect_id="P001", amount=40, bid_type="CB", now=FRI_NOON)
    check_err_contains("HAM CB on own OB", r, "originating manager cannot")

    # ── 3b. Friday Spoiler-Bid Rule ───────────────────────
    print("\n── Friday Spoiler-Bid Rule ──")

    # RV already has prior CBs on P001 (Wed + Thu) → can raise on Friday
    r = am.place_bid(team="RV", prospect_id="P001", amount=40, bid_type="CB", now=FRI_NOON)
    check("RV CB $40 on P001 (Fri, has prior CB)", r["success"], True)

    # CFL already has prior CB on P001 (Thu) → can raise on Friday
    r = am.place_bid(team="CFL", prospect_id="P001", amount=45, bid_type="CB", now=FRI_NOON)
    check("CFL CB $45 on P001 (Fri, has prior CB)", r["success"], True)

    # RV has NO prior CB on P002 → spoiler bid rejected
    r = am.place_bid(team="RV", prospect_id="P003", amount=15, bid_type="CB", now=FRI_NOON)
    check_err_contains("RV spoiler CB on P003 (no prior CB)", r, "no last-minute spoiler")
    # ── 3c. Admin bid management ──────────────────────────
    print("\n── Admin Bid Management ──")

    # Add a corrective admin CB on P003, then update and remove it.
    r = am.admin_add_bid(
        admin="TEST_ADMIN",
        team="HAM",
        prospect_id="P003",
        amount=20,
        bid_type="CB",
        now=THU_NOON,
    )
    check("Admin add bid on P003", r["success"], True)
    admin_bid_id = (r.get("bid") or {}).get("bid_id")
    check("Admin add returned bid_id", bool(admin_bid_id), True)

    r = am.admin_update_bid_amount(
        admin="TEST_ADMIN",
        bid_id=admin_bid_id,
        amount=25,
        now=THU_NOON,
    )
    check("Admin update bid amount", r["success"], True)
    check("Admin update previous amount", r.get("previous_amount"), 20)
    check("Admin update new amount", (r.get("bid") or {}).get("amount"), 25)

    r = am.admin_remove_bid(
        admin="TEST_ADMIN",
        bid_id=admin_bid_id,
        now=THU_NOON,
    )
    check("Admin remove bid", r["success"], True)
    check("Admin remove returns same bid_id", (r.get("removed_bid") or {}).get("bid_id"), admin_bid_id)

    r = am.admin_list_bids(now=THU_NOON)
    remaining_ids = {(b.get("bid_id") or "") for b in r.get("bids", [])}
    check("Removed bid absent from list", admin_bid_id in remaining_ids, False)

    # ── 4. Match / Forfeit (Saturday) ─────────────────────
    print("\n── Match / Forfeit (Saturday noon) ──")

    # Wrong phase check
    r = am.record_match(team="HAM", prospect_id="P001", decision="match", source="discord", now=FRI_NOON)
    check_err_contains("Match on Friday (wrong phase)", r, "only allowed on saturday")

    # HAM matches P001 (high CB = $35 from CFL)
    r = am.record_match(team="HAM", prospect_id="P001", decision="match", source="discord", now=SAT_NOON)
    check("HAM matches P001", r["success"], True)

    # Can't change decision
    r = am.record_match(team="HAM", prospect_id="P001", decision="forfeit", source="discord", now=SAT_NOON)
    check_err_contains("HAM re-decide (already decided)", r, "already recorded")

    # Non-OB manager can't decide
    r = am.record_match(team="CFL", prospect_id="P002", decision="match", source="web", now=SAT_NOON)
    check_err_contains("CFL decide P002 (not OB manager)", r, "only the originating")

    # Invalid decision value
    r = am.record_match(team="RV", prospect_id="P002", decision="maybe", source="web", now=SAT_NOON)
    check_err_contains("Invalid decision value", r, "must be")

    # ── 5. Weekly Resolution (Sunday) ─────────────────────
    print("\n── Weekly Resolution (Sunday 2pm) ──")

    result = am.resolve_week(now=SUN_2PM)
    check("resolve status", result["status"], "resolved")

    winners = result.get("winners", {})

    # P001: HAM matched CFL's high CB at $45 (CFL raised on Fri)
    check("P001 winner", winners.get("P001", {}).get("team"), "HAM")
    check("P001 amount", winners.get("P001", {}).get("amount"), 45)

    # P002: RV uncontested OB → wins at flat $10
    check("P002 winner", winners.get("P002", {}).get("team"), "RV")
    check("P002 amount", winners.get("P002", {}).get("amount"), 10)

    # P003: CFL uncontested OB → wins at flat $10
    check("P003 winner", winners.get("P003", {}).get("team"), "CFL")
    check("P003 amount", winners.get("P003", {}).get("amount"), 10)

    # ── 6. Post-resolution checks ─────────────────────────
    print("\n── Post-Resolution Verification ──")

    # WizBucks debited correctly
    wb = json.loads((data_dir / "wizbucks.json").read_text())
    check("HAM WB ($130 - $45 = $85)",  wb.get("Hammers"),            85)
    check("RV WB ($145 - $10 = $135)",  wb.get("Rick Vaughn"),        135)
    check("CFL WB ($125 - $10 = $115)", wb.get("Country Fried Lamb"), 115)

    # Players assigned to winning teams
    players = json.loads((data_dir / "combined_players.json").read_text())
    by_upid = {p["upid"]: p for p in players}
    check("P001 assigned to HAM", by_upid["P001"].get("manager"), "Hammers")
    check("P001 contract = Purchased Contract", by_upid["P001"].get("contract_type"), "Purchased Contract")
    check("P002 assigned to RV",  by_upid["P002"].get("manager"), "Rick Vaughn")
    check("P003 assigned to CFL", by_upid["P003"].get("manager"), "Country Fried Lamb")

    # Ledger entries recorded
    ledger = json.loads((data_dir / "wizbucks_transactions.json").read_text())
    check("Ledger has entries", len(ledger) > 0, True)
    # Auction state reset after resolve
    auction_state = json.loads((data_dir / "auction_current.json").read_text())
    check("Auction bids reset after resolve", len(auction_state.get("bids", [])), 0)
    check("Auction matches reset after resolve", len(auction_state.get("matches", [])), 0)

    # ── Summary ───────────────────────────────────────────
    total = passed + failed
    print(f"\n{'=' * 60}")
    if failed:
        print(f"Results: {passed}/{total} passed — {failed} FAILED")
    else:
        print(f"Results: {passed}/{total} passed  🎉 All passed!")
    print(f"Temp dir: {tmp_dir}")

    try:
        shutil.rmtree(tmp_dir)
        print("(cleaned up temp dir)")
    except Exception:
        print(f"(temp dir kept for inspection)")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
