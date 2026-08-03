#!/usr/bin/env python3
"""Fix 8 owned players left with a blank years_simple after a round-4+
keeper-draft pick on 2026-03-08.

Root cause (draft/draft_manager.py, _apply_pick_to_rosters): keeper-draft
picks in rounds 1-3 get years_simple hard-set to "VC 1"; rounds 4+ have no
equivalent branch, so years_simple is left as whatever it already was --
which was blank for these 8. With years_simple blank, the frontend
(fbp-hub js/rosters.js) and Discord bot (commands/trade.py, lookup.py,
roster.py) all fall back to displaying the literal contract_type string
("Keeper Contract") instead of a real contract code.

Confirmed via player_log.json + Zach's ruling (2026-08-03): a keeper-draft
pick is a TC 1, full stop, regardless of what tier the player carried
before the draft (this also overrides the round<=3 "VC 1" assumption's
implicit corollary for round 4+: the code's own comment -- "Rounds 1-3
start at VC 1" -- implies round 4+ defaults to TC 1, which is exactly the
rule Zach confirmed).

Sets years_simple="TC 1" and status="[5] TC1" (matching the status-code
format already used everywhere else for TC1) on exactly these 8 UPIDs,
each guarded to only touch the record if it's still in the expected
broken state (contract_type == "Keeper Contract", years_simple blank) --
skips (does not overwrite) anything that's changed since this was
written, e.g. if the player has since been traded/dropped and some other
process already set a real value.

Run:
    python3 fix_blank_years_simple_2026_08_03.py --dry-run
    python3 fix_blank_years_simple_2026_08_03.py
"""

import json
import sys

COMBINED_PATH = "data/combined_players.json"

# (upid, name) -- name is just for logging/sanity, matching is by upid only.
TARGET_UPIDS = [
    ("3170", "Ryan Helsley"),
    ("3265", "Tanner Scott"),
    ("3840", "Xavier Edwards"),
    ("4022", "Hunter Greene"),
    ("2916", "Luke Weaver"),
    ("3457", "Kyle Harrison"),
    ("2872", "Lars Nootbaar"),
    ("3726", "Ronny Henriquez"),
]

NEW_YEARS_SIMPLE = "TC 1"
NEW_STATUS = "[5] TC1"


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path, data):
    # combined_players.json's established convention is ensure_ascii=True
    # (per this session's earlier data-cleanse work).
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=True)


def main():
    dry_run = "--dry-run" in sys.argv
    print(f"{'DRY RUN — ' if dry_run else ''}Fix blank years_simple on 8 keeper-draft picks\n" + "=" * 78)

    players = _load(COMBINED_PATH)
    by_upid = {str(p.get("upid")): p for p in players}

    applied, skipped = 0, 0

    for upid, expected_name in TARGET_UPIDS:
        p = by_upid.get(upid)
        if p is None:
            print(f"  upid={upid:6} ({expected_name}): NOT FOUND — skipping  [SKIP]")
            skipped += 1
            continue

        name = p.get("name", "")
        ct = p.get("contract_type")
        ys = p.get("years_simple")
        st = p.get("status")

        still_broken = (ct == "Keeper Contract") and not (ys and str(ys).strip())
        if not still_broken:
            print(f"  upid={upid:6} ({name}): no longer matches expected broken state "
                  f"(contract_type={ct!r}, years_simple={ys!r}) — leaving as-is  [SKIP]")
            skipped += 1
            continue

        print(f"  upid={upid:6} ({name}): years_simple {ys!r} -> {NEW_YEARS_SIMPLE!r}, "
              f"status {st!r} -> {NEW_STATUS!r}  [APPLY]")

        if not dry_run:
            p["years_simple"] = NEW_YEARS_SIMPLE
            p["status"] = NEW_STATUS
        applied += 1

    print(f"\n{'=' * 78}")
    print(f"Applied: {applied}  |  skipped: {skipped}")

    if dry_run:
        print("\nDry run — no files written. Re-run without --dry-run to apply.")
        return

    _save(COMBINED_PATH, players)
    print(f"\nWrote {COMBINED_PATH}")


if __name__ == "__main__":
    main()
