#!/usr/bin/env python3
"""Fix two data-cleanse findings from 2026-08-02: literal duplicate array
rows sharing one UPID, and owned/unowned "stub" records with no UPID at all.

See DATA_CLEANSE_COMBINED_PLAYERS_2026_08_02.md for the original findings
and the chat discussion for the full root-cause reasoning. Summary:

Issue A -- duplicate rows (upid 5996, 3825): each real person has two array
objects sharing one upid. One object carries the season/contract/bio data;
the other carries only bbref_id/fangraphs_id/fangraphs_name (added by
scripts/enrich_sfbb_ids.py) under a slightly different (accented) name
spelling, with everything else blank. enrich_sfbb_ids.py didn't create the
duplicate -- it only fills blanks on an already-matched record and never
appends -- but its own upid/mlb_id-keyed lookup dicts silently collapse a
pre-existing duplicate to whichever row comes last, so it only ever
enriched one side. This merges the two into one row per person.

Issue B -- 6 owned/unowned records with upid="" entirely (Ivan Herrera,
Josh Smith, Luis Robert Jr., Michael Harris II, Bobby Witt Jr., Jake
Odorizzi). Root cause confirmed via player_log.json: all 5 owned ones were
dropped by their respective managers at the *identical* timestamp
(2026-03-13T10:42:53, microseconds apart -- one batch event, not five
manager decisions), then re-added by the *same* manager who'd just dropped
them. The re-add's Yahoo-name match failed against the existing rich UPID
record (Ivan vs Iván, missing Jr./II suffixes) and created a bare
ownership-only stub instead of re-linking. upid_database.json's name_index
already resolves every one of these names to the correct original UPID, so
only the roster-sync's own matching missed it.

Fix: transplant FBP_Team/manager from each stub onto the original rich
UPID record, then delete the stub. Per Zach's decision (2026-08-02): KEEP
the existing contract_type/status/years_simple on the rich record rather
than resetting them -- the same manager reclaiming the same player right
after a forced drop reads as a sync glitch, not a new free-agent pickup.
Jake Odorizzi is the same stub pattern but unowned on both sides; only his
`team` field is updated (TEX -> TB, his current team per the stub) since no
ownership is involved.

Every write is guarded by an exact-match check against the expected
pre-fix value. Idempotent -- already-merged records are skipped, not
reprocessed.

Run:
    python3 scripts/fix_duplicate_rows_and_upid_stubs_2026_08_02.py --dry-run
    python3 scripts/fix_duplicate_rows_and_upid_stubs_2026_08_02.py
"""

import json
import sys
from datetime import datetime, timezone

COMBINED_FILE = "data/combined_players.json"
PLAYER_LOG_FILE = "data/player_log.json"

NOW = datetime.now(timezone.utc)
NOW_ISO = NOW.isoformat().replace("+00:00", "Z")
SEASON = 2026
ADMIN = "zpressley"

# ---------------------------------------------------------------------------
# Issue A: duplicate rows sharing one upid. (upid, full_name, sparse_name,
# [crosswalk fields to copy from sparse -> full if full is missing them])
# ---------------------------------------------------------------------------
DUPLICATE_MERGES = [
    ("5996", "Agustin Ramirez", "Agustín Ramírez", ["bbref_id", "fangraphs_id", "fangraphs_name"]),
    ("3825", "Randy Rodriguez", "Randy Rodríguez", ["bbref_id", "fangraphs_id", "fangraphs_name"]),
]

# ---------------------------------------------------------------------------
# Issue B: no-upid stub records. (stub_name, stub_expected_yahoo_id,
# target_upid, is_owned)
# ---------------------------------------------------------------------------
STUB_MERGES = [
    ("Ivan Herrera", "11836", "3513", True),
    ("Josh Smith", "12562", "2774", True),
    ("Luis Robert Jr.", "10765", "2907", True),
    ("Michael Harris II", "12056", "4284", True),
    ("Bobby Witt Jr.", "11771", "4295", True),
    ("Jake Odorizzi", "9310", "2604", False),
]


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path, data, ensure_ascii=True):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=ensure_ascii)


def make_log_entry(player, upid, event, update_type="DataFix"):
    return {
        "id": f"{SEASON}-{NOW_ISO}-UPID_{upid}-{update_type}-DupeStubFix",
        "season": SEASON,
        "source": "manual_data_fix",
        "admin": ADMIN,
        "timestamp": NOW_ISO,
        "upid": upid,
        "player_name": player.get("name") or "",
        "team": player.get("team") or "",
        "pos": player.get("position") or "",
        "age": player.get("age"),
        "level": str(player.get("level") or ""),
        "team_rank": player.get("team_rank"),
        "rank": player.get("rank"),
        "eta": str(player.get("eta") or ""),
        "player_type": player.get("player_type") or "",
        "owner": player.get("manager") or "",
        "contract": player.get("contract_type") or "",
        "status": player.get("status") or "",
        "years": player.get("years_simple") or "",
        "update_type": update_type,
        "event": event,
    }


def main():
    dry_run = "--dry-run" in sys.argv
    print(f"{'DRY RUN — ' if dry_run else ''}Fix duplicate rows + no-UPID stubs\n" + "=" * 78)

    players = _load(COMBINED_FILE)
    player_log = _load(PLAYER_LOG_FILE)
    existing_log_ids = {e.get("id") for e in player_log}

    to_remove_indices = set()
    log_entries_to_add = []

    # ---------------- Issue A: duplicate rows ----------------
    print("\n-- Issue A: duplicate array rows --")
    for upid, full_name, sparse_name, crosswalk_fields in DUPLICATE_MERGES:
        matches = [(i, p) for i, p in enumerate(players) if str(p.get("upid")) == upid]
        full_matches = [(i, p) for i, p in matches if p.get("name") == full_name]
        sparse_matches = [(i, p) for i, p in matches if p.get("name") == sparse_name]

        if len(matches) == 1:
            print(f"  upid {upid}: only 1 row found (already merged?)  [SKIP]")
            continue
        if len(full_matches) != 1 or len(sparse_matches) != 1:
            print(f"  upid {upid}: expected exactly 1 full + 1 sparse row, found "
                  f"{len(full_matches)} full / {len(sparse_matches)} sparse — NOT touching  [SKIP]")
            continue

        full_i, full_p = full_matches[0]
        sparse_i, sparse_p = sparse_matches[0]

        copied = []
        for field in crosswalk_fields:
            if full_p.get(field):
                continue  # already has it, don't overwrite
            val = sparse_p.get(field)
            if val:
                print(f"  upid {upid} ({full_name}): copy {field}={val!r} from sparse row  [APPLY]")
                if not dry_run:
                    full_p[field] = val
                copied.append(field)

        print(f"  upid {upid}: removing sparse row ({sparse_name!r})  [APPLY]")
        to_remove_indices.add(sparse_i)

        entry = make_log_entry(
            full_p, upid,
            f"Data hygiene: merged duplicate array row ({sparse_name!r}, holding "
            f"{','.join(crosswalk_fields)}) into this record ({','.join(copied) or 'no new fields'} copied over); duplicate removed."
        )
        if entry["id"] not in existing_log_ids:
            log_entries_to_add.append(entry)

    # ---------------- Issue B: no-upid stubs ----------------
    print("\n-- Issue B: no-UPID stub records --")
    for stub_name, expected_yahoo, target_upid, is_owned in STUB_MERGES:
        stub_matches = [
            (i, p) for i, p in enumerate(players)
            if p.get("name") == stub_name and not p.get("upid")
        ]
        target_matches = [(i, p) for i, p in enumerate(players) if str(p.get("upid")) == target_upid]

        if not stub_matches:
            print(f"  {stub_name}: no blank-upid stub found (already merged?)  [SKIP]")
            continue
        if len(stub_matches) != 1 or len(target_matches) != 1:
            print(f"  {stub_name}: expected exactly 1 stub + 1 target, found "
                  f"{len(stub_matches)} stub / {len(target_matches)} target — NOT touching  [SKIP]")
            continue

        stub_i, stub_p = stub_matches[0]
        target_i, target_p = target_matches[0]

        if str(stub_p.get("yahoo_id")) != expected_yahoo:
            print(f"  {stub_name}: stub yahoo_id {stub_p.get('yahoo_id')!r} != expected {expected_yahoo!r} — NOT touching  [SKIP]")
            continue

        if is_owned:
            stub_team = stub_p.get("FBP_Team")
            stub_manager = stub_p.get("manager")
            print(f"  {stub_name}: target upid {target_upid} FBP_Team {target_p.get('FBP_Team')!r} -> {stub_team!r}, "
                  f"manager {target_p.get('manager')!r} -> {stub_manager!r}  [APPLY]")
            if not dry_run:
                target_p["FBP_Team"] = stub_team
                target_p["manager"] = stub_manager
            note = (f"Data hygiene: re-linked to UPID {target_upid} after the 2026-03-13 bulk drop "
                    f"+ reclaim by the same manager created a disconnected no-UPID ownership stub "
                    f"(Yahoo name-match miss). Ownership (FBP_Team/manager) restored here; existing "
                    f"contract terms kept as-is per Zach's decision (treated as a sync glitch, not a "
                    f"new pickup). Stub record removed.")
        else:
            stub_team = stub_p.get("team")
            before_team = target_p.get("team")
            print(f"  {stub_name}: target upid {target_upid} team {before_team!r} -> {stub_team!r}  [APPLY]")
            if not dry_run and stub_team and stub_team != before_team:
                target_p["team"] = stub_team
            note = (f"Data hygiene: merged no-UPID stub record into UPID {target_upid} "
                    f"(unowned on both sides; team updated to current: {before_team!r} -> {stub_team!r}). "
                    f"Stub record removed.")

        print(f"  {stub_name}: removing stub row  [APPLY]")
        to_remove_indices.add(stub_i)

        entry = make_log_entry(target_p, target_upid, note)
        if entry["id"] not in existing_log_ids:
            log_entries_to_add.append(entry)

    # ---------------- Apply removals + save ----------------
    print(f"\n{'=' * 78}")
    print(f"Rows to remove: {len(to_remove_indices)}")
    print(f"Player log entries to add: {len(log_entries_to_add)}")

    if dry_run:
        print("\nDry run — no files written. Re-run without --dry-run to apply.")
        return

    new_players = [p for i, p in enumerate(players) if i not in to_remove_indices]
    player_log.extend(log_entries_to_add)

    _save(COMBINED_FILE, new_players, ensure_ascii=True)
    print(f"\nWrote {COMBINED_FILE} ({len(players)} -> {len(new_players)} players)")
    _save(PLAYER_LOG_FILE, player_log, ensure_ascii=False)
    print(f"Wrote {PLAYER_LOG_FILE}")


if __name__ == "__main__":
    main()
