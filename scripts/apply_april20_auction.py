"""Retroactively apply the week of 2026-04-20 auction resolution.

Winners (computed from the April 20 auction state at commit 2c1df57):
  6572 Chase Dollander -> LFB at $25 (CB_WIN: RV OB, LFB high CB $25)
  3444 Daniel Espino   -> DRO at $15 (OB_MATCH: DRO OB, WAR CB $15, DRO matched)
  7284 A.J. Ewing      -> CFL at $10 (OB_ONLY: CFL OB, no challengers)

Run from repo root:
    python scripts/apply_april20_auction.py
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO_ROOT)
sys.path.insert(0, REPO_ROOT)

from wb_ledger import append_transaction

WEEK_START = "2026-04-20"
WEEK_END = "2026-04-26"
EVENT_LABEL = f"Auction {WEEK_START} to {WEEK_END}"
RESOLVE_TS = "2026-04-26T14:00:00+00:00"  # retroactive: Sunday 10am ET

WINNERS = [
    {"upid": "6572", "team": "LFB", "amount": 25, "full_name": "La Flama Blanca"},
    {"upid": "3444", "team": "DRO", "amount": 15, "full_name": "Andromedans"},
    {"upid": "7284", "team": "CFL", "amount": 10, "full_name": "Country Fried Lamb"},
]


def main():
    # ---- Load current data ----
    with open("data/combined_players.json", "r", encoding="utf-8") as f:
        players = json.load(f)

    with open("data/player_log.json", "r", encoding="utf-8") as f:
        player_log = json.load(f)

    with open("config/managers.json", "r", encoding="utf-8") as f:
        mgr_config = json.load(f)
    teams_meta = mgr_config.get("teams", {})

    # ---- Apply each winner ----
    for win in WINNERS:
        upid = win["upid"]
        team = win["team"]
        amount = win["amount"]
        full_name = win["full_name"]

        # Find the player record
        player = None
        for p in players:
            if str(p.get("upid", "")) == upid:
                player = p
                break

        if not player:
            print(f"ERROR: UPID {upid} not found in combined_players.json")
            sys.exit(1)

        if player.get("manager"):
            print(f"SKIP: {player['name']} (UPID {upid}) already owned by {player['manager']}")
            continue

        # Update player ownership
        player["manager"] = full_name
        player["FBP_Team"] = team
        player["owner"] = team
        player["contract_type"] = "Purchased Contract"

        # WB ledger (reads current balance, deducts, writes wallet + ledger)
        entry = append_transaction(
            team=team,
            amount=-amount,
            transaction_type="auction_winner",
            description=f"{EVENT_LABEL}: {player['name']}",
            related_player={"upid": upid, "name": player["name"]},
            metadata={
                "week_start": WEEK_START,
                "prospect_id": upid,
                "amount": amount,
                "source": "auction_resolve_retroactive",
            },
            timestamp=RESOLVE_TS,
        )

        # Player log entry (matches schema from previous auction resolves)
        player_log.append({
            "id": f"2026-{RESOLVE_TS}-UPID_{upid}-Auction-retrofix-wk0420",
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

        print(
            f"OK: {player['name']} (UPID {upid}) -> {team} ({full_name}) "
            f"${amount} WB  (balance: {entry['balance_before']} -> {entry['balance_after']})"
        )

    # ---- Save ----
    with open("data/combined_players.json", "w", encoding="utf-8") as f:
        json.dump(players, f, indent=2, sort_keys=True)

    with open("data/player_log.json", "w", encoding="utf-8") as f:
        json.dump(player_log, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Files updated:")
    print("  data/combined_players.json")
    print("  data/wizbucks.json")
    print("  data/wizbucks_transactions.json")
    print("  data/player_log.json")


if __name__ == "__main__":
    main()
