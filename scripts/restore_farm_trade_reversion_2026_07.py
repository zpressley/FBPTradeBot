"""Restore farm-player ownership wiped by the July 13 stale-snapshot incident.

Background
----------
This is a second, previously-undiscovered symptom of the same root incident
already fixed in restore_pc_bc_corruption_2026_07.py: commit 902c787
("Mid-season updates: player data, graduations, pipeline fixes", 2026-07-13)
substituted a stale ~June 10 backup snapshot of combined_players.json for
the live file. That script fixed a specific enumerated list of contract_type
corruptions. It did NOT address ownership (FBP_Team/manager) reversion,
because at the time no one had flagged it.

Zach cross-checked a personal list of 9 trades against live data on
2026-07-22 and found 8 of them showing the PRE-trade owner instead of the
post-trade owner. Investigation confirmed: every approved trade with
data_applied_at between 2026-06-10 and 2026-07-13T17:34 (i.e. inside the
stale snapshot's window) had its player ownership reverted by 902c787. MLB
legs of those same trades self-healed within a day or two because the daily
Yahoo roster sync (data_pipeline/roster_sync.py) re-asserts the real Yahoo
roster truth regardless of what's in the file. Farm/prospect players have no
such external source of truth -- this site's own JSON is the only record --
so they stayed silently wrong for 2-3 weeks with nothing to self-correct
them. Two MLB players from the same trades (Jake Burger, Teoscar Hernandez)
also currently show no owner, but that's confirmed to be a real, unrelated
later Yahoo drop (see player_log "In Season Drop" entries in June), not
corruption -- explicitly NOT touched by this script.

This script, for each of the 5 affected trades:
  1. Guards every FBP_Team/manager write with an exact-match check against
     the known-corrupted value (captured at diagnosis time, 2026-07-22).
     If a field has since changed to something else, it's left alone and
     reported as SKIPPED rather than overwritten.
  2. Re-adds the exact original "Trade" player_log entries wiped by the
     same commit, pulled verbatim from commit bda546b (the last commit
     before 902c787) -- not reconstructed, the literal original JSON
     objects -- appended only if no entry with that exact id already
     exists (idempotent).

Explicitly OUT OF SCOPE (confirmed fine, not touched):
  - Jake Burger (upid 2588) and Teoscar Hernandez (upid 1980): both MLB
    legs of these same trades. FBP_Team is currently "" for both, but
    player_log confirms real "In Season Drop" events afterward (Burger
    6/6, Hernandez 5/21 and 5/25 in one case predating the trade) -- they
    were legitimately dropped by whoever rostered them, unrelated to the
    corruption.
  - TRADE-250426_2025-024 (Cam Caminiti, LFB->HAM): a different, unrelated
    situation -- status is still "pending" with only the initiator (LFB)
    having ever accepted. HAM never accepted, so this was never approved
    or applied in the first place; there's nothing to restore. It's well
    past its expires_at and will be swept to "expired" by the new hourly
    trade_expiry_sweep_tick.

Run:
    python3 scripts/restore_farm_trade_reversion_2026_07.py --dry-run
    python3 scripts/restore_farm_trade_reversion_2026_07.py
"""

import json
import sys

COMBINED_FILE = "data/combined_players.json"
PLAYER_LOG_FILE = "data/player_log.json"

# upid -> (known_corrupted_FBP_Team, known_corrupted_manager, correct_FBP_Team, correct_manager)
FIELD_FIXES = {
    "7968": ("LFB", "La Flama Blanca", "DRO", "Andromedans"),        # Andrew Fischer
    "7964": ("WIZ", "Whiz Kids", "CFL", "Country Fried Lamb"),       # Xavier Neyens
    "7796": ("WIZ", "Whiz Kids", "CFL", "Country Fried Lamb"),       # Kevin Defrank
    "7532": ("HAM", "Hammers", "DRO", "Andromedans"),                # Ronny Cruz
    "7647": ("HAM", "Hammers", "DRO", "Andromedans"),                # Dakota Jordan
    "3499": ("LFB", "La Flama Blanca", "DRO", "Andromedans"),        # Harry Ford
    "6725": ("LFB", "La Flama Blanca", "DRO", "Andromedans"),        # Jaxon Wiggins
    "7146": ("WIZ", "Whiz Kids", "DRO", "Andromedans"),              # Eduardo Tait
}

# Player_log entries deleted by the corruption, restored verbatim from
# commit bda546b (immediately before the corrupting commit 902c787).
RESTORED_LOG_ENTRIES = [
    {
        "id": "2026-2026-06-24T22:35:34.648466Z-UPID_7968-Trade-TRADE-240626_1721-047",
        "season": 2026, "source": "trade_portal", "admin": "WIZ",
        "timestamp": "2026-06-24T22:35:34.648466Z", "upid": "7968",
        "player_name": "Andrew Fischer", "team": "MIL", "pos": "3B", "age": 21,
        "level": "", "team_rank": None, "rank": None, "eta": "", "player_type": "Farm",
        "owner": "Andromedans", "contract": "Development Cont.", "status": "[7] P",
        "years": "P", "update_type": "Trade", "event": "TRADE-240626_1721-047: LFB->DRO",
    },
    {
        "id": "2026-2026-07-01T02:27:21.287549Z-UPID_7964-Trade-TRADE-300626_2226-048",
        "season": 2026, "source": "trade_portal", "admin": "WIZ",
        "timestamp": "2026-07-01T02:27:21.287549Z", "upid": "7964",
        "player_name": "Xavier Neyens", "team": "HOU", "pos": "SS", "age": 19,
        "level": "", "team_rank": None, "rank": None, "eta": "", "player_type": "Farm",
        "owner": "Country Fried Lamb", "contract": "Blue Chip Contract", "status": "[7] P",
        "years": "P", "update_type": "Trade", "event": "TRADE-300626_2226-048: WIZ->CFL",
    },
    {
        "id": "2026-2026-07-01T02:27:21.287549Z-UPID_7796-Trade-TRADE-300626_2226-048",
        "season": 2026, "source": "trade_portal", "admin": "WIZ",
        "timestamp": "2026-07-01T02:27:21.287549Z", "upid": "7796",
        "player_name": "Kevin Defrank", "team": "MIA", "pos": "SP", "age": 17,
        "level": "", "team_rank": None, "rank": None, "eta": "", "player_type": "Farm",
        "owner": "Country Fried Lamb", "contract": "Development Cont.", "status": "[7] P",
        "years": "P", "update_type": "Trade", "event": "TRADE-300626_2226-048: WIZ->CFL",
    },
    {
        "id": "2026-2026-07-05T22:17:24.953619Z-UPID_7532-Trade-TRADE-050726_1803-049",
        "season": 2026, "source": "trade_portal", "admin": "WIZ",
        "timestamp": "2026-07-05T22:17:24.953619Z", "upid": "7532",
        "player_name": "Ronny Cruz", "team": "CHC", "pos": "SS", "age": 19,
        "level": "", "team_rank": None, "rank": None, "eta": "", "player_type": "Farm",
        "owner": "Andromedans", "contract": "Purchased Contract", "status": "[7] P",
        "years": "P", "update_type": "Trade", "event": "TRADE-050726_1803-049: HAM->DRO",
    },
    {
        "id": "2026-2026-07-05T22:17:24.953619Z-UPID_7647-Trade-TRADE-050726_1803-049",
        "season": 2026, "source": "trade_portal", "admin": "WIZ",
        "timestamp": "2026-07-05T22:17:24.953619Z", "upid": "7647",
        "player_name": "Dakota Jordan", "team": "SF", "pos": "OF", "age": 22,
        "level": "", "team_rank": None, "rank": None, "eta": "", "player_type": "Farm",
        "owner": "Andromedans", "contract": "Development Cont.", "status": "[7] P",
        "years": "P", "update_type": "Trade", "event": "TRADE-050726_1803-049: HAM->DRO",
    },
    {
        "id": "2026-2026-07-06T23:20:04.591953Z-UPID_3499-Trade-TRADE-060726_1757-052",
        "season": 2026, "source": "trade_portal", "admin": "SAD",
        "timestamp": "2026-07-06T23:20:04.591953Z", "upid": "3499",
        "player_name": "Harry Ford", "team": "SEA", "pos": "C", "age": 22,
        "level": "", "team_rank": None, "rank": 471, "eta": "", "player_type": "Farm",
        "owner": "Andromedans", "contract": "Purchased Contract", "status": "[7] P",
        "years": "P", "update_type": "Trade", "event": "TRADE-060726_1757-052: LFB->DRO",
    },
    {
        "id": "2026-2026-07-06T23:20:04.591953Z-UPID_6725-Trade-TRADE-060726_1757-052",
        "season": 2026, "source": "trade_portal", "admin": "SAD",
        "timestamp": "2026-07-06T23:20:04.591953Z", "upid": "6725",
        "player_name": "Jaxon Wiggins", "team": "CHC", "pos": "SP", "age": 24,
        "level": "", "team_rank": None, "rank": None, "eta": "", "player_type": "Farm",
        "owner": "Andromedans", "contract": "Purchased Contract", "status": "[7] P",
        "years": "P", "update_type": "Trade", "event": "TRADE-060726_1757-052: LFB->DRO",
    },
    {
        "id": "2026-2026-07-06T23:31:18.323741Z-UPID_7146-Trade-TRADE-060726_1927-053",
        "season": 2026, "source": "trade_portal", "admin": "WIZ",
        "timestamp": "2026-07-06T23:31:18.323741Z", "upid": "7146",
        "player_name": "Eduardo Tait", "team": "PHI", "pos": "C", "age": 19,
        "level": "", "team_rank": None, "rank": None, "eta": "", "player_type": "Farm",
        "owner": "Andromedans", "contract": "Development Cont.", "status": "[7] P",
        "years": "P", "update_type": "Trade", "event": "TRADE-060726_1927-053: WIZ->DRO",
    },
]


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    dry_run = "--dry-run" in sys.argv

    players = _load(COMBINED_FILE)
    player_log = _load(PLAYER_LOG_FILE)
    by_upid = {str(p.get("upid")): p for p in players}

    print(f"{'DRY RUN — ' if dry_run else ''}Farm-player trade-reversion repair\n" + "=" * 70)

    applied, skipped = 0, 0
    for upid, (bad_team, bad_mgr, good_team, good_mgr) in FIELD_FIXES.items():
        player = by_upid.get(upid)
        if not player:
            print(f"upid {upid}: NOT FOUND in {COMBINED_FILE} — skipping entirely")
            continue
        name = player.get("name", "?")
        cur_team = player.get("FBP_Team")
        cur_mgr = player.get("manager")
        if cur_team == bad_team and cur_mgr == bad_mgr:
            print(f"  {name:20} FBP_Team/manager  {cur_team!r}/{cur_mgr!r} -> {good_team!r}/{good_mgr!r}  [APPLY]")
            if not dry_run:
                player["FBP_Team"] = good_team
                player["manager"] = good_mgr
            applied += 1
        elif cur_team == good_team and cur_mgr == good_mgr:
            print(f"  {name:20} already correct ({cur_team!r}/{cur_mgr!r})  [SKIP]")
            skipped += 1
        else:
            print(f"  {name:20} unexpected current value {cur_team!r}/{cur_mgr!r} "
                  f"(expected corrupted {bad_team!r}/{bad_mgr!r}) — NOT touching  [SKIP]")
            skipped += 1

    print("\n" + "=" * 70)
    print("Player_log restoration")
    existing_ids = {e.get("id") for e in player_log}
    log_added = 0
    for entry in RESTORED_LOG_ENTRIES:
        if entry["id"] in existing_ids:
            print(f"  {entry['player_name']:20} log entry already present  [SKIP]")
            continue
        print(f"  {entry['player_name']:20} restoring log entry ({entry['event']})  [APPLY]")
        if not dry_run:
            player_log.append(entry)
        log_added += 1

    print("\n" + "=" * 70)
    print(f"Fields applied: {applied}  |  Fields skipped: {skipped}  |  Log entries restored: {log_added}")

    if dry_run:
        print("\nDry run — no files written. Re-run without --dry-run to apply.")
        return

    with open(COMBINED_FILE, "w", encoding="utf-8") as f:
        json.dump(players, f, indent=2, ensure_ascii=False)
    with open(PLAYER_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(player_log, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {COMBINED_FILE} and {PLAYER_LOG_FILE}.")


if __name__ == "__main__":
    main()
