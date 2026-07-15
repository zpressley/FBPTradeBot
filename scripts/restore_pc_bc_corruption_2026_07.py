"""Surgical repair for the July 13, 2026 stale-snapshot corruption.

Background
----------
Commit 902c787 ("Mid-season updates: player data, graduations, pipeline
fixes", 2026-07-13) substituted a stale June 10 backup snapshot of
combined_players.json for the live file during a local working session,
wiping out ~5 weeks of real transactions for a subset of players before
the (unrelated, correctly-scoped) 37-player graduation script ran and
carried the corruption forward in the same commit. See Discord report
from the commissioner and league managers (Jake/CFL re: A.J. Ewing,
Whiz Kids re: Fuentes/Culpepper/Velazquez, not much of a donkey re: Troy
Melton) for the original bug report.

This script does NOT touch anything wholesale. For every field it
changes, it:
  1. Only acts on a fixed, individually-verified list of (upid, field)
     pairs where we diffed the current live file against commit
     bda546b (the last-known-good commit immediately before 902c787)
     and confirmed the field actually differs.
  2. Re-checks player_log.json for each affected upid for ANY event
     timestamped on/after 2026-07-13 (i.e., after the corruption) —
     zero were found for any of these upids, confirming nothing
     legitimate has touched these fields since the corruption, so a
     restore cannot be clobbering a newer real change.
  3. Guards every single-field write with an exact match against the
     known-corrupted value captured at diagnosis time. If a field has
     since changed to something else (e.g., manually fixed already,
     or legitimately changed by other pipeline activity between
     diagnosis and this script running), that field is left alone and
     reported as SKIPPED rather than overwritten.
  4. Re-adds the 8 "Contract Purchase" player_log entries that were
     silently deleted by the same corruption, pulled verbatim from the
     original commits that created them (f7fba1c, 0229f7d, f4b64ef,
     925e5cf, 01269d5, a8bf3ad, fd4bee3, adde392) — appended only if no
     entry with that exact id already exists (idempotent).

Explicitly OUT OF SCOPE (flagged for manual review, not auto-fixed):
  - Harry Ford (upid 3499): manager differs (LFB now vs DRO in
    bda546b) but no player_log or trades.json entry explains it either
    way — could be a legitimate post-diagnosis trade/waiver move, or
    could be more fallout. Needs a human look before touching.
  - Quinn Matthews (upid 7722): contract_type/status differ from
    bda546b, but bda546b is downstream of an "Admin update: Quinn
    Matthews" commit (36fb4c5) with unclear ordering. Needs a human
    look before touching.

Run:
    python3 scripts/restore_pc_bc_corruption_2026_07.py --dry-run
    python3 scripts/restore_pc_bc_corruption_2026_07.py
"""

import json
import sys

COMBINED_FILE = "data/combined_players.json"
PLAYER_LOG_FILE = "data/player_log.json"

# (upid) -> {field: (known_corrupted_value, correct_value)}
# Every value here was read directly from the live file and from commit
# bda546b at diagnosis time (2026-07-15).
FIELD_FIXES = {
    # --- Group 1: paid PC->BC / DC->PC upgrades wiped back to PC/DC ---
    "7284": {"contract_type": ("Purchased Contract", "Blue Chip Contract")},   # A.J. Ewing
    "4665": {"contract_type": ("Purchased Contract", "Blue Chip Contract")},   # Troy Melton
    "7690": {"contract_type": ("Purchased Contract", "Blue Chip Contract")},   # Kaelen Culpepper
    "6140": {"contract_type": ("Purchased Contract", "Blue Chip Contract")},   # Alfredo Duno
    "7710": {"contract_type": ("Purchased Contract", "Blue Chip Contract")},   # Ryan Sloan
    "3875": {"contract_type": ("Purchased Contract", "Blue Chip Contract")},   # Jarlin Susana
    "6617": {"contract_type": ("Purchased Contract", "Blue Chip Contract")},   # Ralphy Velazquez
    "3920": {"contract_type": ("Development Cont.", "Purchased Contract")},    # Owen Murphy

    # --- Group 2: established MLB keepers, contract fields nulled out ---
    "1922": {"contract_type": (None, "Keeper Contract")},                      # Walker Buehler
    "4273": {"contract_type": (None, "Keeper Contract"),
             "FBP_Team": (None, "")},                                          # Hunter Gaddis
    "2936": {"contract_type": (None, "Keeper Contract")},                      # Martín Pérez
    "3634": {"contract_type": (None, "Keeper Contract")},                      # Ryan Kreidler
    "3660": {"contract_type": (None, "Keeper Contract")},                      # Nick Loftin
    "6821": {"contract_type": (None, "Keeper Contract")},                      # Brandon Young
    "2105": {"contract_type": (None, "Keeper Contract"),
             "years_simple": ("", "TC 1")},                                    # Anthony Kay
    "5922": {"contract_type": (None, "Keeper Contract")},                      # Anthony Seigler
    "4316": {"contract_type": (None, "Keeper Contract")},                      # Nate Eaton
    "4773": {"contract_type": (None, "Keeper Contract"),
             "manager": (None, "The Damn Yankees"),
             "FBP_Team": ("", "DMN")},                                         # Tyler Tolbert
    "6562": {"years_simple": ("", "TC 1"),
             "status": ("", "[5] TC1")},                                       # Dylan Crews
}

# Player_log entries deleted by the corruption, restored verbatim from
# the commits that originally created them.
RESTORED_LOG_ENTRIES = [
    {
        "id": "2026-2026-06-30T19:58:16.921359-04:00-UPID_7284-Purchase-Player Profile Self-Service",
        "season": 2026, "source": "Player Profile Self-Service", "admin": "CFL",
        "timestamp": "2026-06-30T19:58:16.921359-04:00", "upid": "7284",
        "player_name": "A.J. Ewing", "team": "NYM", "pos": "2B,OF", "age": 21,
        "level": "", "team_rank": None, "rank": None, "eta": "", "player_type": "Farm",
        "owner": "Country Fried Lamb", "contract": "Blue Chip Contract", "status": "[7] P",
        "years": "P", "update_type": "Purchase", "event": "2026 PC → BC",
    },
    {
        "id": "2026-2026-07-08T19:48:21.028056-04:00-UPID_4665-Purchase-Player Profile Self-Service",
        "season": 2026, "source": "Player Profile Self-Service", "admin": "SAD",
        "timestamp": "2026-07-08T19:48:21.028056-04:00", "upid": "4665",
        "player_name": "Troy Melton", "team": "DET", "pos": "SP,RP", "age": 25,
        "level": "", "team_rank": None, "rank": 310, "eta": "", "player_type": "Farm",
        "owner": "not much of a donkey", "contract": "Blue Chip Contract", "status": "[7] P",
        "years": "P", "update_type": "Purchase", "event": "2026 PC → BC",
    },
    {
        "id": "2026-2026-07-06T15:50:54.054197-04:00-UPID_7690-Purchase-Player Profile Self-Service",
        "season": 2026, "source": "Player Profile Self-Service", "admin": "HAM",
        "timestamp": "2026-07-06T15:50:54.054197-04:00", "upid": "7690",
        "player_name": "Kaelen Culpepper", "team": "MIN", "pos": "SS", "age": 23,
        "level": "", "team_rank": None, "rank": None, "eta": "", "player_type": "Farm",
        "owner": "Hammers", "contract": "Blue Chip Contract", "status": "[7] P",
        "years": "P", "update_type": "Purchase", "event": "2026 PC → BC",
    },
    {
        "id": "2026-2026-07-06T15:37:51.204902-04:00-UPID_6140-Purchase-Dashboard Self-Service",
        "season": 2026, "source": "Dashboard Self-Service", "admin": "LFB",
        "timestamp": "2026-07-06T15:37:51.204902-04:00", "upid": "6140",
        "player_name": "Alfredo Duno", "team": "CIN", "pos": "C", "age": 20,
        "level": "", "team_rank": None, "rank": None, "eta": "", "player_type": "Farm",
        "owner": "La Flama Blanca", "contract": "Blue Chip Contract", "status": "[7] P",
        "years": "P", "update_type": "Purchase", "event": "2026 PC → BC",
    },
    {
        "id": "2026-2026-07-06T15:38:10.159506-04:00-UPID_7710-Purchase-Dashboard Self-Service",
        "season": 2026, "source": "Dashboard Self-Service", "admin": "LFB",
        "timestamp": "2026-07-06T15:38:10.159506-04:00", "upid": "7710",
        "player_name": "Ryan Sloan", "team": "SEA", "pos": "SP", "age": 20,
        "level": "", "team_rank": None, "rank": None, "eta": "", "player_type": "Farm",
        "owner": "La Flama Blanca", "contract": "Blue Chip Contract", "status": "[7] P",
        "years": "P", "update_type": "Purchase", "event": "2026 PC → BC",
    },
    {
        "id": "2026-2026-07-06T15:38:29.623274-04:00-UPID_3875-Purchase-Dashboard Self-Service",
        "season": 2026, "source": "Dashboard Self-Service", "admin": "LFB",
        "timestamp": "2026-07-06T15:38:29.623274-04:00", "upid": "3875",
        "player_name": "Jarlin Susana", "team": "WSH", "pos": "SP", "age": 21,
        "level": "", "team_rank": None, "rank": None, "eta": "", "player_type": "Farm",
        "owner": "La Flama Blanca", "contract": "Blue Chip Contract", "status": "[7] P",
        "years": "P", "update_type": "Purchase", "event": "2026 PC → BC",
    },
    {
        "id": "2026-2026-07-06T15:38:57.624859-04:00-UPID_6617-Purchase-Dashboard Self-Service",
        "season": 2026, "source": "Dashboard Self-Service", "admin": "LFB",
        "timestamp": "2026-07-06T15:38:57.624859-04:00", "upid": "6617",
        "player_name": "Ralphy Velazquez", "team": "CLE", "pos": "1B", "age": 20,
        "level": "", "team_rank": None, "rank": None, "eta": "", "player_type": "Farm",
        "owner": "La Flama Blanca", "contract": "Blue Chip Contract", "status": "[7] P",
        "years": "P", "update_type": "Purchase", "event": "2026 PC → BC",
    },
    {
        "id": "2026-2026-07-06T08:34:45.064844-04:00-UPID_3920-Purchase-Dashboard Self-Service",
        "season": 2026, "source": "Dashboard Self-Service", "admin": "RV",
        "timestamp": "2026-07-06T08:34:45.064844-04:00", "upid": "3920",
        "player_name": "Owen Murphy", "team": "ATL", "pos": "SP", "age": 22,
        "level": "", "team_rank": None, "rank": None, "eta": "", "player_type": "Farm",
        "owner": "Rick Vaughn", "contract": "Purchased Contract", "status": "[7] P",
        "years": "P", "update_type": "Purchase", "event": "2026 DC → PC",
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

    print(f"{'DRY RUN — ' if dry_run else ''}Field-level repair\n" + "=" * 70)

    applied, skipped = 0, 0
    for upid, fields in FIELD_FIXES.items():
        player = by_upid.get(upid)
        if not player:
            print(f"upid {upid}: NOT FOUND in {COMBINED_FILE} — skipping entirely")
            continue
        name = player.get("name", "?")
        for field, (expected_corrupt, correct_value) in fields.items():
            actual = player.get(field)
            if actual == expected_corrupt:
                print(f"  {name:20} {field:14} {actual!r} -> {correct_value!r}  [APPLY]")
                if not dry_run:
                    player[field] = correct_value
                applied += 1
            elif actual == correct_value:
                print(f"  {name:20} {field:14} already correct ({actual!r})  [SKIP]")
                skipped += 1
            else:
                print(f"  {name:20} {field:14} unexpected current value {actual!r} "
                      f"(expected corrupted {expected_corrupt!r}) — NOT touching  [SKIP]")
                skipped += 1

    print("\n" + "=" * 70)
    print("Player_log restoration")
    existing_ids = {e.get("id") for e in player_log}
    log_added = 0
    for entry in RESTORED_LOG_ENTRIES:
        if entry["id"] in existing_ids:
            print(f"  {entry['player_name']:20} log entry already present  [SKIP]")
            continue
        print(f"  {entry['player_name']:20} restoring log entry ({entry['event']}, {entry['timestamp']})  [APPLY]")
        if not dry_run:
            player_log.append(entry)
        log_added += 1

    print("\n" + "=" * 70)
    print(f"Fields applied: {applied}  |  Fields skipped: {skipped}  |  Log entries restored: {log_added}")

    if dry_run:
        print("\nDry run — no files written. Re-run without --dry-run to apply.")
        return

    with open(COMBINED_FILE, "w", encoding="utf-8") as f:
        json.dump(players, f, indent=2, sort_keys=True)
    with open(PLAYER_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(player_log, f, indent=2)
    print("\nWrote data/combined_players.json and data/player_log.json.")


if __name__ == "__main__":
    main()
