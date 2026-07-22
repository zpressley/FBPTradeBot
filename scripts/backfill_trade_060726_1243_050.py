"""Backfill for TRADE-060726_1243-050 (WIZ / CFL), stuck since 2026-07-06.

Background
----------
WIZ proposed: WIZ sends Cody Bellinger (upid 1883) to CFL; CFL sends Steele
Hall (upid 7951) and Max Clark (upid 6566) to WIZ. WIZ (the initiator)
accepted at creation time. CFL also accepted, but that acceptance's git
commit was lost to the fire-and-forget commit bug (see _maybe_commit fix in
trade/trade_store.py), so the stored record reverted to only showing WIZ's
acceptance and the trade never reached admin_review. The Discord admin-review
card looked stuck ("did not go through correctly").

Investigation turned up a wrinkle: the Bellinger leg (an MLB player, tracked
via live Yahoo roster sync) already shows CFL as owner in combined_players.json
today. Git history confirms Bellinger genuinely moved WIZ -> CFL via Yahoo's
real roster sync on 2026-07-08 (a player_log "In Season Add" entry existed
briefly, then got wiped as an unrelated side effect of the
"Backfill auction week 07/06/2026 results" commit on 2026-07-14 — the
FBP_Team field itself was untouched by that, only the log line was lost).
Farm/prospect players (Hall, Clark) have no Yahoo equivalent — they can only
move via this site's own trade portal, which is exactly what got stuck.

So: only the two farm-player legs are genuinely still outstanding. This
script:
  1. Verifies Bellinger (1883) is already CFL-owned (skips it, no-op,
     confirms nothing to do there).
  2. Moves Hall (7951) and Clark (6566) from CFL to WIZ, guarded by an
     exact-match check on current owner (won't touch if reality has
     already changed since diagnosis).
  3. Appends player_log entries for the two farm-player moves.
  4. Marks the trade record itself resolved (status "approved") with a
     data_applied_summary noting the Bellinger leg was already settled via
     Yahoo, not by this backfill.

Idempotent: guarded by trade rec's data_applied_at, and by exact-match
ownership checks before each player move.

Run:
    python3 scripts/backfill_trade_060726_1243_050.py --dry-run
    python3 scripts/backfill_trade_060726_1243_050.py
"""

import json
import sys
from datetime import datetime, timezone

TRADE_ID = "TRADE-060726_1243-050"
TRADES_FILE = "data/trades.json"
COMBINED_FILE = "data/combined_players.json"
PLAYER_LOG_FILE = "data/player_log.json"
WIZBUCKS_FILE = "data/wizbucks.json"

# (upid, expected_current_owner, new_owner)
FARM_MOVES = [
    ("7951", "CFL", "WIZ"),  # Steele Hall
    ("6566", "CFL", "WIZ"),  # Max Clark
]
ALREADY_RESOLVED_VIA_YAHOO = [
    ("1883", "CFL"),  # Cody Bellinger — WIZ -> CFL already happened on Yahoo 2026-07-08
]

ADMIN = "ADMIN_BACKFILL"


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _iso_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main():
    dry_run = "--dry-run" in sys.argv
    print(f"{'DRY RUN — ' if dry_run else ''}Backfill {TRADE_ID}\n" + "=" * 70)

    trades = _load(TRADES_FILE)
    rec = trades.get(TRADE_ID)
    if not rec:
        print(f"ERROR: {TRADE_ID} not found in {TRADES_FILE}")
        sys.exit(1)

    if rec.get("data_applied_at"):
        print(f"SKIP: {TRADE_ID} already has data_applied_at={rec['data_applied_at']} — nothing to do.")
        return

    status = rec.get("status")
    if status not in ("pending", "partial_accept", "admin_review"):
        print(f"ERROR: {TRADE_ID} status is {status!r}, expected an active status. Aborting — needs manual review.")
        sys.exit(1)

    players = _load(COMBINED_FILE)
    by_upid = {str(p.get("upid")): p for p in players}
    wizbucks = _load(WIZBUCKS_FILE)
    player_log = _load(PLAYER_LOG_FILE)

    # Confirm the already-Yahoo-resolved leg really is resolved; don't touch it.
    for upid, expected_owner in ALREADY_RESOLVED_VIA_YAHOO:
        p = by_upid.get(upid)
        if not p:
            print(f"  upid {upid}: NOT FOUND — cannot confirm Yahoo resolution. Aborting.")
            sys.exit(1)
        owner = str(p.get("FBP_Team") or "").strip().upper()
        if owner != expected_owner:
            print(
                f"  upid {upid} ({p.get('name')}): expected already-resolved owner {expected_owner}, "
                f"found {owner!r} instead — reality has changed since diagnosis. Aborting for manual review."
            )
            sys.exit(1)
        print(f"  {p.get('name'):20} already {expected_owner} via Yahoo roster sync — leaving untouched  [SKIP]")

    # Managers config for franchise display names
    managers_cfg = _load("config/managers.json")
    teams_cfg = (managers_cfg or {}).get("teams") or {}

    def franchise_name(abbr: str) -> str:
        meta = teams_cfg.get(abbr) or {}
        name = meta.get("name")
        return name if name and name in wizbucks else abbr

    now = _iso_now()
    season_year = 2026
    moved = 0
    log_added = 0

    for upid, expected_owner, new_owner in FARM_MOVES:
        p = by_upid.get(upid)
        if not p:
            print(f"  upid {upid}: NOT FOUND in {COMBINED_FILE} — aborting for manual review.")
            sys.exit(1)
        owner = str(p.get("FBP_Team") or "").strip().upper()
        if owner != expected_owner:
            print(
                f"  upid {upid} ({p.get('name')}): expected current owner {expected_owner}, found {owner!r} — "
                "reality has changed since diagnosis. Aborting for manual review."
            )
            sys.exit(1)

        print(f"  {p.get('name'):20} {expected_owner} -> {new_owner}  [APPLY]")
        if not dry_run:
            p["FBP_Team"] = new_owner
            p["manager"] = franchise_name(new_owner)

            entry = {
                "id": f"{season_year}-{now}-UPID_{upid}-Trade-{TRADE_ID}",
                "season": season_year,
                "source": "trade_portal_backfill",
                "admin": ADMIN,
                "timestamp": now,
                "upid": upid,
                "player_name": p.get("name") or "",
                "team": p.get("team") or "",
                "pos": p.get("position") or "",
                "age": p.get("age"),
                "level": str(p.get("level") or ""),
                "team_rank": p.get("team_rank"),
                "rank": p.get("rank"),
                "eta": str(p.get("eta") or ""),
                "player_type": p.get("player_type") or "",
                "owner": p.get("manager") or "",
                "contract": p.get("contract_type") or "",
                "status": p.get("status") or "",
                "years": p.get("years_simple") or "",
                "update_type": "Trade",
                "event": f"{TRADE_ID}: {expected_owner}->{new_owner} (manual backfill — stuck trade, see WARP notes)",
            }
            player_log.append(entry)
            log_added += 1
        moved += 1

    rec["status"] = "approved"
    rec["admin_decision_by"] = ADMIN
    rec["processed_at"] = now
    rec["acceptances"] = sorted(set((rec.get("acceptances") or []) + ["CFL"]))
    rec["data_applied_at"] = now
    rec["data_applied_by"] = ADMIN
    rec["data_applied_summary"] = {
        "player_moves": moved,
        "player_log_entries": log_added,
        "pick_moves": 0,
        "wb_transfers": 0,
        "buyins_purchased": 0,
        "warnings": [
            "Bellinger (upid 1883) leg already resolved via Yahoo roster sync on 2026-07-08; "
            "not re-applied or re-logged by this backfill.",
            "Manually backfilled 2026-07-22 after the fire-and-forget git-commit bug lost CFL's "
            "acceptance; see trade_store._maybe_commit fix.",
        ],
    }

    print("\n" + "=" * 70)
    print(f"Player moves applied: {moved}  |  Log entries added: {log_added}")
    print(f"Trade status -> approved (data_applied_at={now})")

    if dry_run:
        print("\nDry run — no files written. Re-run without --dry-run to apply.")
        return

    trades[TRADE_ID] = rec
    with open(TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2)
    with open(COMBINED_FILE, "w", encoding="utf-8") as f:
        json.dump(players, f, indent=2)
    with open(PLAYER_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(player_log, f, indent=2)

    print(f"\nWrote {TRADES_FILE}, {COMBINED_FILE}, {PLAYER_LOG_FILE}.")


if __name__ == "__main__":
    main()
