"""Backfill auction resolutions for weeks 2026-06-22 and 2026-06-29.

Why this exists
---------------
Weekly auction resolution runs from the Discord bot's Sunday tick and an
embedded step in the daily pipeline; both missed these two weeks:

- 2026-06-22: DMN placed an uncontested OB on 8355 (Parks Harber) @ $10.
  The bid was wiped from auction_current.json when the week rolled over
  without resolving (recovered from git snapshot ea3ed5f). Fully unapplied.
- 2026-06-29: JEP OB on 6574 (Yohandy Morales) @ $10 and DMN OB on 4579
  (Mitch Bratt) @ $10, both uncontested. Ownership + player_log were applied
  on 2026-07-05, but WizBucks were never charged.

Idempotent by design:
- Ownership is assigned only if the prospect is currently unowned.
- WizBucks are charged only if no auction_winner ledger entry exists for the upid.
- A player_log entry is appended only if none exists for that upid + event.

Run from repo root:
    python scripts/backfill_auction_late_june_2026.py
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO_ROOT)
sys.path.insert(0, REPO_ROOT)

from wb_ledger import append_transaction

RESOLVED_WEEK = "2026-06-29"

WINNERS = [
    {
        "upid": "8355", "team": "DMN", "amount": 10, "full_name": "The Damn Yankees",
        "week_start": "2026-06-22", "week_end": "2026-06-28",
        "resolve_ts": "2026-06-28T14:00:00+00:00", "log_suffix": "retrofix-wk0622",
    },
    {
        "upid": "6574", "team": "JEP", "amount": 10, "full_name": "Jepordizers!",
        "week_start": "2026-06-29", "week_end": "2026-07-05",
        "resolve_ts": "2026-07-05T14:00:00+00:00", "log_suffix": "retrofix-wk0629",
    },
    {
        "upid": "4579", "team": "DMN", "amount": 10, "full_name": "The Damn Yankees",
        "week_start": "2026-06-29", "week_end": "2026-07-05",
        "resolve_ts": "2026-07-05T14:00:00+00:00", "log_suffix": "retrofix-wk0629",
    },
]


def _event_label(win):
    return f"Auction {win['week_start']} to {win['week_end']}"


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    players = _load("data/combined_players.json")
    player_log = _load("data/player_log.json")
    players_by_upid = {str(p.get("upid", "")): p for p in players}

    for win in WINNERS:
        upid, team, amount = win["upid"], win["team"], win["amount"]
        event_label = _event_label(win)
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
                description=f"{event_label}: {player.get('name', '')}",
                related_player={"upid": upid, "name": player.get("name", "")},
                metadata={
                    "week_start": win["week_start"],
                    "prospect_id": upid,
                    "amount": amount,
                    "source": "auction_resolve_backfill",
                },
                timestamp=win["resolve_ts"],
            )
            charged = f"{entry['balance_before']} -> {entry['balance_after']}"
        else:
            charged = "already charged (skipped)"

        # 3) player_log — only if no Auction entry for this upid + event
        has_log = any(
            str(e.get("upid")) == upid
            and e.get("event") == event_label
            and e.get("update_type") == "Auction"
            for e in player_log
        )
        if not has_log:
            player_log.append({
                "id": f"2026-{win['resolve_ts']}-UPID_{upid}-Auction-{win['log_suffix']}",
                "season": 2026,
                "source": "Auction Resolve",
                "admin": "bot",
                "timestamp": win["resolve_ts"],
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
                "event": event_label,
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
    # Match the existing on-disk format (escaped non-ASCII) to keep the diff
    # limited to the appended entry rather than reformatting the whole file.
    with open("data/player_log.json", "w", encoding="utf-8") as f:
        json.dump(player_log, f, indent=2)

    # Advance the weekly resolve guard
    with open("data/auction_resolved_state.json", "w", encoding="utf-8") as f:
        json.dump({"resolved_week": RESOLVED_WEEK}, f, indent=2)

    # Clear stale OB bids and mark the 06-29 week resolved so the portal
    # stops showing already-owned prospects as active auctions.
    cur = _load("data/auction_current.json")
    if cur.get("week_start") == RESOLVED_WEEK:
        winners_summary = {
            w["upid"]: {
                "team": w["team"],
                "amount": w["amount"],
                "name": players_by_upid[w["upid"]].get("name", ""),
            }
            for w in WINNERS if w["week_start"] == RESOLVED_WEEK
        }
        cur["bids"] = []
        cur["matches"] = []
        cur["phase"] = "processing"
        cur["resolved_at"] = "2026-07-05T14:00:00+00:00"
        cur["resolved_winners"] = winners_summary
        cur["last_updated"] = "2026-07-05T14:00:00+00:00"
        with open("data/auction_current.json", "w", encoding="utf-8") as f:
            json.dump(cur, f, indent=2, sort_keys=True)
        print(f"auction_current.json: week {RESOLVED_WEEK} marked resolved; cleared stale bids")
    else:
        print(f"auction_current.json: week is {cur.get('week_start')} (not {RESOLVED_WEEK}); left untouched")

    print("\nDone.")


if __name__ == "__main__":
    main()
