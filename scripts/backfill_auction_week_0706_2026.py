"""Backfill auction resolution for week 2026-07-06.

Why this exists
---------------
Weekly auction resolution missed this week: 3 uncontested OB bids were
never resolved because the week rolled straight into the 2026-07-13
All-Star break, and resolve_week()'s _is_week_active() gate now returns
False for any date in [all_star_break_start, auction_restart) — so the
tick loop, /api/admin/auction/resolve-now, and resolve-auction.yml all
silently no-op on this week until 2026-07-20. See
AUCTION_RESOLUTION_RELIABILITY.md for the underlying race-condition fix;
this script is the one-off catch-up for the week that got stuck before
that fix landed.

Pending bids (verified 2026-07-14, all uncontested, all still unowned,
all affordable):
  - RV  OB $10 on 7922 (Christian Zazueta) — balance $90
  - CFL OB $10 on 7261 (Nolan Perry)       — balance $60
  - DMN OB $10 on 6007 (Abimelec Ortiz)    — balance $20

Idempotent by design, same pattern as scripts/backfill_auction_late_june_2026.py:
- Ownership is assigned only if the prospect is currently unowned.
- WizBucks are charged only if no auction_winner ledger entry exists for the upid.
- A player_log entry is appended only if none exists for that upid + event.

Run from repo root:
    python scripts/backfill_auction_week_0706_2026.py
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO_ROOT)
sys.path.insert(0, REPO_ROOT)

from wb_ledger import append_transaction

RESOLVED_WEEK = "2026-07-06"
WEEK_END = "2026-07-12"
RESOLVE_TS = "2026-07-12T14:00:00+00:00"

WINNERS = [
    {"upid": "7922", "team": "RV", "amount": 10, "full_name": "Rick Vaughn"},
    {"upid": "7261", "team": "CFL", "amount": 10, "full_name": "Country Fried Lamb"},
    {"upid": "6007", "team": "DMN", "amount": 10, "full_name": "The Damn Yankees"},
]

EVENT_LABEL = f"Auction {RESOLVED_WEEK} to {WEEK_END}"


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    players = _load("data/combined_players.json")
    player_log = _load("data/player_log.json")
    players_by_upid = {str(p.get("upid", "")): p for p in players}

    for win in WINNERS:
        upid, team, amount = win["upid"], win["team"], win["amount"]
        player = players_by_upid.get(upid)
        if not player:
            print(f"ERROR: UPID {upid} not found in combined_players.json")
            sys.exit(1)

        # 1) Ownership — only if currently unowned
        if not player.get("manager"):
            player["manager"] = win["full_name"]
            player["FBP_Team"] = team
            player["owner"] = team
            player["contract_type"] = "Purchased Contract"
            assigned = True
        else:
            assigned = False

        # 2) WizBucks — only if not already charged for this prospect
        ledger = _load("data/wizbucks_transactions.json")
        already_charged = any(
            e.get("transaction_type") == "auction_winner"
            and str((e.get("related_player") or {}).get("upid")) == upid
            for e in ledger
        )
        if not already_charged:
            entry = append_transaction(
                team=team,
                amount=-amount,
                transaction_type="auction_winner",
                description=f"{EVENT_LABEL}: {player.get('name', '')}",
                related_player={"upid": upid, "name": player.get("name", "")},
                metadata={
                    "week_start": RESOLVED_WEEK,
                    "prospect_id": upid,
                    "amount": amount,
                    "source": "auction_resolve_backfill",
                },
                timestamp=RESOLVE_TS,
            )
            charged = f"{entry['balance_before']} -> {entry['balance_after']}"
        else:
            charged = "already charged (skipped)"

        # 3) player_log — only if no Auction entry for this upid + event
        has_log = any(
            str(e.get("upid")) == upid
            and e.get("event") == EVENT_LABEL
            and e.get("update_type") == "Auction"
            for e in player_log
        )
        if not has_log:
            player_log.append({
                "id": f"2026-{RESOLVE_TS}-UPID_{upid}-Auction-retrofix-wk0706",
                "season": 2026,
                "source": "Auction Resolve",
                "admin": "bot",
                "timestamp": RESOLVE_TS,
                "upid": upid,
                "player_name": player.get("name", ""),
                "team": player.get("team", ""),
                "pos": player.get("position", ""),
                "age": player.get("age"),
                "level": player.get("level", ""),
                "team_rank": None,
                "rank": None,
                "eta": player.get("eta", ""),
                "player_type": player.get("player_type", "Farm"),
                "owner": team,
                "contract": "Purchased Contract",
                "status": player.get("status", ""),
                "years": "P",
                "update_type": "Auction",
                "event": EVENT_LABEL,
            })
            logged = True
        else:
            logged = False

        print(
            f"{upid} {player.get('name', '')}: assigned={assigned} "
            f"wb={charged} logged={logged} -> {team} (${amount})"
        )

    # Persist players + log
    with open("data/combined_players.json", "w", encoding="utf-8") as f:
        json.dump(players, f, indent=2, sort_keys=True)
    with open("data/player_log.json", "w", encoding="utf-8") as f:
        json.dump(player_log, f, indent=2)

    # Advance the weekly resolve guard
    with open("data/auction_resolved_state.json", "w", encoding="utf-8") as f:
        json.dump({"resolved_week": RESOLVED_WEEK}, f, indent=2)

    # Clear stale OB bids and mark the week resolved so the portal stops
    # showing already-owned prospects as active auctions.
    cur = _load("data/auction_current.json")
    if cur.get("week_start") == RESOLVED_WEEK:
        winners_summary = {
            w["upid"]: {
                "team": w["team"],
                "amount": w["amount"],
                "name": players_by_upid[w["upid"]].get("name", ""),
            }
            for w in WINNERS
        }
        cur["bids"] = []
        cur["matches"] = []
        cur["phase"] = "processing"
        cur["resolved_at"] = RESOLVE_TS
        cur["resolved_winners"] = winners_summary
        cur["last_updated"] = RESOLVE_TS
        with open("data/auction_current.json", "w", encoding="utf-8") as f:
            json.dump(cur, f, indent=2, sort_keys=True)
        print(f"auction_current.json: week {RESOLVED_WEEK} marked resolved; cleared stale bids")
    else:
        print(f"auction_current.json: week is {cur.get('week_start')} (not {RESOLVED_WEEK}); left untouched")

    print("\nDone.")


if __name__ == "__main__":
    main()
